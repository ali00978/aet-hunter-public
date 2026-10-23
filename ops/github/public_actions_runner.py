"""Owner-gated GitHub Actions executor for the unchanged private Hunter.

This public file holds NO credentials or stored advert data. All actual
source, provider data and scan output stay in the original private repo.
"""
import os


def main() -> None:
    # Fail closed: this must be an intentional switch on the owner repository.
    if os.environ.get("AET_PUBLIC_HUNTER_ENABLED") != "true":
        print("Public GitHub scan not enabled; no marketplace access")
        return
    if os.environ.get("GITHUB_REPOSITORY") != "ali00978/aet-hunter-public":
        raise RuntimeError("Refusing to run in an unknown GitHub repository")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Refusing non-main execution of production code")

    from ops.modal.legacy_bridge import run_original_hunter, verify_credentials
    env = dict(os.environ)
    # The established bridge has its own fail-closed production switch.
    # Explicitly set it only after the public owner switch and credentials pass.
    verify_credentials(env)
    env["AET_MODAL_LIVE_ENABLED"] = "I_ACCEPT_LIVE_SCANS"
    run_original_hunter(env)


if __name__ == "__main__":
    main()
