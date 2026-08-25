import sys
import pathlib
import yaml

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])

KEEP_VERBATIM = {"Resolve build parameters"}

doc = yaml.safe_load(src.read_text())
job = doc["jobs"]["build"]

job["runs-on"] = "ubuntu-latest"
job.pop("timeout-minutes", None)

new_steps = []
for step in job["steps"]:
    name = step.get("name", "<unnamed>")
    if "uses" in step:
        step = {
            "name": name,
            "shell": "bash",
            "run": f'echo "STEP_RAN::{name}"',
        }
        new_steps.append(step)
        continue
    if name in KEEP_VERBATIM:
        step["run"] = f'echo "STEP_RAN::{name}"\n' + step["run"]
        new_steps.append(step)
        continue
    step.pop("uses", None)
    step["run"] = f'echo "STEP_RAN::{name}"'
    new_steps.append(step)

new_steps.append(
    {
        "name": "Dump resolved environment",
        "if": "always()",
        "shell": "bash",
        "run": (
            'for k in CHANNEL VERSION MANIFEST_REF BUILD_ID SDK_DIR SEC_SDK_DIR '
            'DEV_S3_PREFIX SEC_S3_PREFIX RAUC_BUNDLE RK_IMAGE_VERSION; do\n'
            '  printf "RESOLVED::%s=%s\\n" "$k" "${!k-<unset>}"\n'
            "done\n"
        ),
    }
)

job["steps"] = new_steps
text = yaml.safe_dump(doc, sort_keys=False, width=10000)
if text.startswith("name:"):
    text = text.replace("\ntrue:\n", "\non:\n", 1)
else:
    text = text.replace("true:\n", "on:\n", 1)
dst.write_text(text)
assert "\non:\n" in text, "on: key was not restored"
print(f"wrote {dst} ({len(new_steps)} steps)")
