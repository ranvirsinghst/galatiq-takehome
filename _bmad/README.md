# Minimal BMAD helper installation

The official shared helper scripts are vendored here, including workflow rendering, setup, memory-log and configuration helpers, with their upstream tests. This is not a full BMAD framework installation and is not part of the invoice processor runtime.

Source: [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD/tree/b0d29434d06b7c99df7bcc6b47de6216f2472449/skills/bmad/scripts), pinned to commit `b0d29434d06b7c99df7bcc6b47de6216f2472449` from `dev`. No `6.13.0-next` or `v6.13.0-next` tag was available when fetched. Upstream currently stores these helpers under `skills/bmad/scripts` rather than the former `src/scripts` path. Files are unmodified; SHA-256 hashes are recorded in [provenance.json](provenance.json). The upstream [MIT license](LICENSE) is included.

Requires Python 3.11+; all dependencies are Python standard library. `config_utils.py` is the shared upstream dependency for the configuration resolvers. `config.toml` is local project configuration. Its `core.output_folder` directs planning artifacts to `docs`.

Run from the repository root:

```sh
python _bmad/scripts/resolve_config.py --project-root .
python _bmad/scripts/resolve_config.py --project-root . --key core.output_folder
python _bmad/scripts/resolve_customization.py --project-root . --skill /absolute/path/to/installed/bmad-spec
python _bmad/scripts/memlog.py --help
```

`resolve_customization.py` reads the installed skill's `customize.toml`; skill files remain in the user's plugin installation. Team overrides can be added in `_bmad/custom/` if needed. These helpers are pinned for reproducibility; they do not auto-update.

Build setup was restored using the pinned official `skills/bmad/SKILL.md` and `references/setup.md` setup flow. The missing core skill was staged outside the repository; `setup.py --list-config-questions` returned `[]`, then setup copied the complete shared-script tree. Existing project answers were retained and upstream configuration defaults filled. No plugin cache files were modified.

To render the installed build workflow, run from the repository root:

```sh
uv run --no-cache _bmad/scripts/render_skill.py --project-root "$PWD" --skill /absolute/path/to/installed/bmad-build
```

Read the absolute workflow path emitted by that command. Generated snapshots under `_bmad/render/` are local runtime outputs. The upstream renderer tests assume an upstream checkout layout. To run against the installed plugin without modifying upstream tests, set their `SKILLS_SRC` module constant before discovery:

```sh
uv run --no-cache python - <<'PYTEST'
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path("_bmad/scripts/tests").resolve()))
import test_render_skill
test_render_skill.SKILLS_SRC = Path("/absolute/path/to/pinned/upstream/skills")
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover("_bmad/scripts/tests"))
raise SystemExit(not result.wasSuccessful())
PYTEST
```

The unmodified upstream `assets/config.template.toml` is included because upstream renderer tests read it relative to the shared-script tree.

Use the matching pinned upstream skill fixtures for the complete helper test suite: installed walkthrough/retrospective skills predate the upstream renderer entrypoints. The installed `bmad-build` entrypoint itself renders successfully.
