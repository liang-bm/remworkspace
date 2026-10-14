# Security

请不要在公开 Issue 中发布漏洞细节、密钥或私人记录。优先使用仓库 Security 页面上的私密报告入口；没有入口时，先向维护者索取私下联系方式，只公开提出联系请求。

脆弱性の詳細、認証情報、個人の記録を公開 Issue に載せないでください。Security 画面に非公開の報告機能があれば使ってください。利用できない場合は、詳細を出さずにメンテナーへ非公開の連絡方法を確認してください。

## What to report

Report problems such as unintended disclosure of local records, credential handling failures, unsafe file access, unexpected command execution or a bypass of protected-resource checks. Ordinary UI bugs and feature requests belong in public issues after removing private information.

Security fixes target the current `main` source branch. This first source publication has no separately supported older release series. Third-party model servers, MCP servers and operating-system services also have their own security policies.

## Report privately

1. Open the repository's Security tab. If a “Report a vulnerability” button is available, use GitHub's private reporting form.
2. If that option is unavailable, use a private contact channel the maintainer has published on their GitHub profile. If none is listed, open an issue asking for a private security contact without including the vulnerability, exploit, affected file contents or personal data. Wait for a private channel before sending details.
3. Include the affected source commit or app version, operating system, impact and minimal reproduction steps using invented data. Explain any permissions or external services needed to reproduce it.

Never send a real API key, private chat, diary, voice model, resource-vault key or recovery archive. Reduce a reproduction to synthetic files and redact usernames, account identifiers and absolute personal paths from logs. If a credential has already been exposed, revoke it with its provider; deleting it from a message or repository does not revoke it.

Committing this file does not enable private vulnerability reporting. Use the private reporting button only when GitHub actually shows it. There is no promised response time or independent security-audit certification.

## Data and protection limits

Chat and diary databases are stored locally but do not currently have database-level encryption. Remote chat providers receive the messages and context used for their requests; online speech recognition receives recordings. Local-first storage does not make remote requests private from the selected provider.

API credentials use current-user Windows DPAPI or Linux Secret Service. Private resource vaults use AES-256-GCM and account-protected keys. Voice files may be decrypted into an account-only temporary directory while a local engine uses them. These controls do not prevent the logged-in account or an administrator from inspecting active files, and forced termination or power loss may leave temporary files behind. They do not grant rights to character or voice material.

Keep personal data, credentials, live configuration, model weights, vaults and recovery material outside public Git history. Protect local backups and the operating-system account separately. See the README's [privacy section](README.en.md#data-backups-and-privacy) for storage locations and remote-request behavior.
