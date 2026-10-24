# CLAUDE.md

Projektregeln für Claude Code. Die vollständigen Hinweise stehen in **[AGENTS.md](AGENTS.md)** – bitte zuerst lesen.

@AGENTS.md
@CONTRACT.md

Kurzfassung:
- Schnittstellen nur über CONTRACT.md ändern und dann alle Dienste anpassen.
- UI und Doku auf Deutsch.
- GPU-Images: CUDA ≤ 12.4.
- Keine Zugangsdaten ins Repo oder in Logs.
- Vor dem Abschluss: `corepack pnpm build` im Frontend, betroffene Container mit `docker compose up -d --build <dienst>` starten und end-to-end prüfen.
