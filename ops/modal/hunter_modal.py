"""Modal deploy definition; production code stays in private GitHub."""

import modal

app = modal.App("aet-cars-private-hunter")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ca-certificates")
    .add_local_dir("ops/modal", remote_path="/root/aet-modal")
)

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("aet-hunter-runtime")],
    schedule=modal.Cron("17 */2 * * *", timezone="UTC"),
    cpu=1,
    memory=2048,
    timeout=2100,
    max_containers=1,
)
def run_every_two_hours():
    import sys
    sys.path.insert(0, "/root/aet-modal")
    from legacy_bridge import run_original_hunter
    run_original_hunter()
