"""Production Hunter schedules on Modal, never on GitHub-hosted Actions.

Private scanner state is stored only in a Modal Volume, never in GitHub.
The original PRIVATE repository is not cloned, read or modified by this app.
"""
import modal

app = modal.App("aet-cars-public-hunter")
state = modal.Volume.from_name("aet-cars-public-hunter-state", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .add_local_dir(".", remote_path="/root/aet-public",
                        ignore=[".git", ".github", "__pycache__", "*.json", "*.pyc"]))
secret = modal.Secret.from_name("aet-hunter-runtime")


def _run(platform):
    import os
    import sys
    sys.path.insert(0, "/root/aet-public")
    os.environ["AET_HUNTER_STATE_DIR"] = "/mnt/aet-hunter-state"
    from public_hunter_runtime import run_platform
    return run_platform(platform, persist=state.commit, reload=state.reload)


@app.function(
    image=image,
    secrets=[secret],
    volumes={"/mnt/aet-hunter-state": state},
    schedule=modal.Cron("17 */2 * * *", timezone="UTC"),
    timeout=1800,
    max_containers=1,
)
def marktplaats():
    """Scan every two hours, using only the public scanner source."""
    return _run("marktplaats")


@app.function(
    image=image,
    secrets=[secret],
    volumes={"/mnt/aet-hunter-state": state},
    schedule=modal.Cron("47 2,14 * * *", timezone="UTC"),
    timeout=300,
    max_containers=1,
)
def facebook():
    """Separate provider run, limited to 2/day and explicit spend control."""
    return _run("facebook")
