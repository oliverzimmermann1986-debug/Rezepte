"""Recipes-Subsystem: Indexer, Zutaten-Normalisierung, Einkaufskorb-Logik.

Aufteilung:
  canonical.py — Normalisierung von Zutaten-Namen (Plural→Singular, Synonyme)
  units.py     — Einheiten-Klassen + sichere Konvertierung
  cart_logic.py — Smart-Merge beim Hinzufügen zum Einkaufskorb
  indexer.py   — FS→DB-Sync + Background-Job für KI-Zutaten-Extraktion
"""
