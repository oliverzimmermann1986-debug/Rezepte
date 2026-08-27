# Rezepte 1.5.4

## Behoben

- Der atomare Proxmox-Updater hält die absoluten Python-Shebangs installierter
  Konsolenskripte nach dem Venv-Tausch gültig. `yt-dlp` ist dadurch direkt nach
  jedem Update wieder ausführbar.
- Das Deployment prüft `yt-dlp` nun vor dem Dienstneustart und rollt bei einem
  defekten Entry-Point automatisch auf den vorherigen Stand zurück.
