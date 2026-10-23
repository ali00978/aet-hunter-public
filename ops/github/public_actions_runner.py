"""Owner-gated GitHub Actions executor for the unchanged private Hunter.

This public file holds NO credentials or stored advert data. All actual
source, provider data and scan output stay in the original private repo.
"""
import os


def main() -> None:
    # Fail closed: this must be an intentional switch on the owner repository.
    if os.environ.get("AET_GITHUB_TERMS_APPROVED") != "true":
        print("Awaiting explicit GitHub hosted-runner usage approval; no live scan")
        return
    if os.environ.get("AET_PUBLIC_HUNTER_ENABLED") != "true":
        print("Public GitHub scan not enabled; no marketplace access")
        return
    if os.environ.get("GITHUB_REPOSITORY") != "ali00978/aet-hunter-public":
        raise RuntimeError("Refusing to run in an unknown GitHub repository")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Refusing non-main execution of production code")

    platform = os.environ.get("AET_HUNTER_PLATFORM", "")
    if platform not in ("marktplaats", "facebook"):
        raise RuntimeError("Must select exactly one allowed Hunter platform")

    from ops.modal.legacy_bridge import run_original_hunter, verify_credentials
    env = dict(os.environ)
    # Marketplaces are isolated: the Marktplaats job must not receive or need
    # an Apify token. Facebook receives only its separate provider secret.
    verify_credentials(env, platform=platform)
    env["AET_MODAL_LIVE_ENABLED"] = "I_ACCEPT_LIVE_SCANS"
    run_original_hunter(env, platform=platform)


if __name__ == "__main__":
    main()
