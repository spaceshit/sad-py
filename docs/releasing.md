# Release procedure

1. Select and verify an available PyPI distribution name; `sad-api-discovery` is the proposed name. Confirm ownership and fill in repository URLs once the GitHub repository exists.
2. Review coverage claims, licenses, dependency versions and the changelog. Set matching versions in pyproject.toml and sad.__version__. Configure private vulnerability reporting.
3. Run lint, formatting, typing, tests and wheel/sdist checks. Install the wheel into a clean virtual environment and run `sad --version` plus example analysis.
4. Push the reviewed release commit and `vX.Y.Z` tag. The release workflow verifies the version, repeats checks, builds distributions and creates a **draft** GitHub release with attached artifacts.
5. Configure a PyPI trusted publisher for this repository, `publish.yml`, and the `pypi` GitHub environment. Protect that environment with reviewer approval. Publish the reviewed GitHub release to trigger the separate publishing workflow. TestPyPI rehearsal is recommended before the first production release.

The workflows are templates until repository permissions and PyPI ownership are configured. No credentials are stored in this project and no publishing occurs on pull requests. Publishing rebuilds and validates the tagged source, then uses OIDC trusted publishing. See the [PyPI trusted publisher documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/) and the [official setup-python action](https://github.com/actions/setup-python).
