# Contributing to RemWorkspace

Bug reports, translations, documentation fixes and focused code changes are welcome. You can write an issue or pull request in Chinese, English or Japanese.

RemWorkspace is the repository name. The application is currently called RemChat, and Python imports use `remcompanion`. Keep existing executable names, imports and storage paths unless a change includes a migration plan.

## Report a problem or suggest a feature

Search [existing issues](https://github.com/liang-bm/remworkspace/issues) first. For a bug, include the app version or source commit, your operating system, the steps to reproduce it, and what you expected to happen. A short recording or screenshot can help with a UI problem, but remove names, local paths, chats and other private information before attaching it.

For a feature, describe what you are trying to do and where the current app gets in the way. A concrete example helps more than a list of unrelated features.

Follow [SECURITY.md](SECURITY.md) for vulnerabilities or exposed credentials. Do not put security details or private data in a public issue.

## Work on the source

Start with the source setup in the [Chinese](README.md#从源码运行), [English](README.en.md#run-from-source) or [Japanese](README.ja.md#ソースから起動する) README. The public repository has no character assets, voice models or personal configuration. Supply only assets you have permission to use and keep them out of commits. Core unit tests do not require a running chat model or the desktop pet's artwork.

Create a branch from `main` for one change. Names such as `fix/diary-search`, `feature/keyboard-shortcut` or `docs/linux-setup` are enough. Keep refactoring unrelated to the change in a separate pull request.

The main source directories are:

| Directory | What belongs here |
| --- | --- |
| `app/remcompanion/` | Application logic and UI |
| `app/remcompanion/platforms/` | Native Windows and Linux adapters |
| `platforms/` | Platform launch, setup and packaging scripts |
| `config/defaults/` | Neutral configuration defaults |
| `tests/` | Maintained tests using synthetic data |
| `scripts/` | Maintenance and source-publication tools |

## Check your change

Use Python 3.12. From the project root, run the existing unit suite with your development environment's interpreter.

PowerShell:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'app'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

Bash:

```bash
PYTHONPATH=app PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Add or update a small regression test when behavior changes. Use invented records and temporary directories. Keep useful regression tests; remove disposable debugging scripts and generated outputs before submitting.

Unit tests do not prove that a window renders correctly or that microphone, keyring, GPU and voice setup work on another computer. For UI or native changes, describe the platforms you actually tried, include the exact interaction, and note any platform you could not test. For a visible UI change, attach a redacted before-and-after screenshot when it helps explain the result.

If you change a feature's location or operation, update the matching instructions in all three READMEs. Keep button labels consistent with the app, including Chinese labels that are not yet translated.

## Open a pull request

Explain what changed, why, and how you checked it. Link the related issue if there is one. Mention changes to storage formats, defaults or system permissions so reviewers can check compatibility.

Check your diff before pushing. Do not commit API keys, live configuration, `data/`, chat or diary databases, backups, voice files, trained weights, resource vaults, personal screenshots or runtime environments. Ignored files can still be added explicitly, so check the actual files you are sending.

Contribute only code and material you have the right to share. Preserve existing license notices. Generated images, character art and voice assets are not covered by the application's MIT license.
