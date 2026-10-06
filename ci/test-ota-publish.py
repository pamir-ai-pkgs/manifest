"""Offline gates for publishing existing signed DVT artifacts."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "publisher", Path(__file__).with_name("ota-publish.py")
)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublishTests(unittest.TestCase):
    def test_rc_version_and_dvt_artifact_prefix_are_preserved(self):
        p = publisher.plan("rk3576-v0.2.0-rc.3", "12345-2", "dev")
        self.assertEqual(p["version"], "0.2.0-rc.3")
        self.assertEqual(
            p["prefix"], "pamir-rk3576/candidates/rk3576-v0.2.0-rc.3-dvt/12345-2"
        )
        self.assertEqual(p["bundle"], "lapis-dvt-dev-v0.2.0-rc.3.raucb")
        self.assertEqual(p["checksum_asset"], "SHA256SUMS-dvt")

    def test_production_artifacts_use_secure_prefix_and_sit_identity(self):
        p = publisher.plan("rk3576-v0.2.0", "12345-1", "sit")
        self.assertEqual(p["prefix"], "pamir-rk3576/releases/rk3576-v0.2.0-dvt-sec")
        self.assertEqual(p["bundle"], "lapis-dvt-sec-v0.2.0.raucb")
        self.assertEqual(p["compatible"], "pamir-lapis-rk3576")
        self.assertEqual(p["checksum_asset"], "SHA256SUMS-dvt-secure")

    def test_reject_unscoped_or_unsafe_publications(self):
        for tag, build, channel in [
            ("main", "1-1", "dev"),
            ("rk3576-v0.2.0-nightly.1", "1-1", "dev"),
            ("rk3576-v0.2.0-rc.3", "1-1", "sit"),
            ("rk3576-v0.2.0", "1-1", "prod"),
            ("rk3576-v0.2.0", "../../other", "dev"),
            ("rk3576-v0.2.0;echo x", "1-1", "dev"),
        ]:
            with (
                self.subTest(tag=tag, build=build, channel=channel),
                self.assertRaises(ValueError),
            ):
                publisher.plan(tag, build, channel)

    def test_checksum_rejects_corrupt_or_ambiguous_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "bundle"
            f.write_bytes(b"signed fixture")
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            sums = f"{digest}  IMAGES/bundle.raucb\n"
            self.assertEqual(
                publisher.verify_checksum(f, sums, "IMAGES/bundle.raucb"), digest
            )
            f.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                publisher.verify_checksum(f, sums, "IMAGES/bundle.raucb")
            with self.assertRaises(ValueError):
                publisher.verify_checksum(f, sums + sums, "IMAGES/bundle.raucb")

    def test_manifest_must_pin_each_publisher_input(self):
        xml = (
            "<manifest>"
            + "".join(
                f'<project name="{name}" revision="' + "a" * 40 + '"/>'
                for name in ["linux-rockchip-bsp-tools", "linux-rockchip-mkosi"]
            )
            + "</manifest>"
        )
        self.assertEqual(publisher.source_pins(xml)["linux-rockchip-mkosi"], "a" * 40)
        with self.assertRaises(ValueError):
            publisher.source_pins(xml.replace("a" * 40, "main"))
        with self.assertRaises(ValueError):
            publisher.source_pins("<manifest/>")

    def test_offer_must_match_bundle_and_uboot_before_idempotent_skip(self):
        offer = {
            "update_available": True,
            "update": {
                "version": "0.2.0-rc.3",
                "sha256": "a",
                "size": 12,
                "uboot": {"sha256": "b", "size": 4},
            },
        }
        publisher.validate_offer(offer, "0.2.0-rc.3", "a", 12, "b", 4)
        with self.assertRaises(ValueError):
            publisher.validate_offer(offer, "0.2.0-rc.3", "wrong", 12, "b", 4)
        with self.assertRaises(ValueError):
            publisher.validate_offer(offer, "0.2.0", "a", 12, "b", 4)

    def test_bundle_metadata_rejects_wrong_version_and_posture(self):
        selected = publisher.plan("rk3576-v0.2.0-rc.3", "1-1", "dev")
        info = {"version": "v0.2.0-rc.3", "compatible": "pamir-lapis-rk3576-dev"}
        publisher.validate_bundle(info, selected)
        for changed in [
            dict(info, version="v0.2.0"),
            dict(info, compatible="pamir-lapis-rk3576"),
        ]:
            with self.assertRaises(ValueError):
                publisher.validate_bundle(changed, selected)

    def test_signed_manifest_rejects_tampering_and_wrong_target(self):
        import base64
        import json
        import subprocess

        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            private = work / "private.pem"
            public = work / "public.der"
            subprocess.run(
                [
                    "openssl",
                    "ecparam",
                    "-name",
                    "prime256v1",
                    "-genkey",
                    "-noout",
                    "-out",
                    str(private),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "openssl",
                    "pkey",
                    "-in",
                    str(private),
                    "-pubout",
                    "-outform",
                    "DER",
                    "-out",
                    str(public),
                ],
                check=True,
            )
            payload = {"board": "lapis", "channel": "dev", "update_available": False}
            data = work / "original.json"
            data.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            sig = work / "original.sig"
            subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-sign",
                    str(private),
                    "-out",
                    str(sig),
                    str(data),
                ],
                check=True,
            )
            result = dict(
                payload,
                signature={
                    "alg": "ecdsa-p256-sha256-der",
                    "value": base64.b64encode(sig.read_bytes()).decode(),
                },
            )
            publisher.verify_manifest(work, result, public, "dev")
            with self.assertRaises(ValueError):
                publisher.verify_manifest(work, result, public, "sit")
            with self.assertRaises(subprocess.CalledProcessError):
                publisher.verify_manifest(
                    work, dict(result, update_available=True), public, "dev"
                )


if __name__ == "__main__":
    unittest.main()
