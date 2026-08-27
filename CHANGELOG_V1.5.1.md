# Rezepte 1.5.1

## Behoben

- Der systemd-Webdienst startet Uvicorn jetzt ueber `python -m uvicorn`.
  Damit bleibt der Dienst nach dem atomaren Wechsel von `venv.next` nach
  `venv` startfaehig; absolute Shebang-Pfade aus dem Staging-Slot koennen den
  Produktionsstart nicht mehr blockieren.
