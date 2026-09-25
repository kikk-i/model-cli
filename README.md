# Model CLI

Prosty terminalowy interfejs do rozmowy z lokalnymi modelami obsługiwanymi przez `mlx-vlm`. Skrypt uruchamia serwer na `127.0.0.1:8080`, wyświetla odpowiedzi na bieżąco i zapisuje historię rozmowy na dysku.

## Wymagania

- Python 3 i pakiet `rich` do uruchomienia interfejsu.
- Polecenie `uv` dostępne w `PATH`. Skrypt używa go do uruchomienia `mlx-vlm` (`uv run --with mlx-vlm ...`).
- Polecenia systemowe `lsof` i `ps` (używane przy zatrzymywaniu serwera).
- Lokalne pliki modeli w katalogu LM Studio, w tym `config.json` w katalogu każdego modelu. Domyślne nazwy modeli podano w sekcji [Zmiana modeli](#zmiana-modeli).
- Środowisko, w którym działają wybrane modele oraz `mlx-vlm`. Sam skrypt nie pobiera modeli.

Możesz uruchamiać skrypt bez instalowania `rich` na stałe:

```bash
uv run --with rich python model.py --help
```

Poniższe przykłady używają krótkiego polecenia `model`. Aby działało w bieżącej sesji powłoki, uruchom w katalogu projektu:

```bash
MODEL_CLI_PATH="$PWD/model.py"
model() { uv run --with rich python "$MODEL_CLI_PATH" "$@"; }
```

Zamiast `model ...` można za każdym razem wpisać `uv run --with rich python model.py ...` z katalogu projektu.

## Polecenia

| Polecenie | Działanie |
| --- | --- |
| `model help` lub `model --help` | Wyświetla pomoc. |
| `model load --4b` | Uruchamia serwer z modelem przypisanym do klucza `4b`. |
| `model load --9b` | Uruchamia serwer z modelem przypisanym do klucza `9b`. |
| `model load --4b --no-mtp` | Uruchamia model bez akceleracji MTP. Analogicznie działa `--9b`. |
| `model unload` | Zatrzymuje serwer i usuwa plik jego stanu. |
| `model status` | Pokazuje stan serwera, aktywny model i statystyki ostatniej rozmowy. |
| `model --q treść pytania` | Zadaje pytanie. Używa aktualnie uruchomionego modelu, a jeśli żaden nie działa, wybiera `4b`. |
| `model --q --4b treść pytania` | Zadaje pytanie modelowi `4b`; `--9b` wybiera model `9b`. |
| `model --q --code treść pytania` | Dodaje instrukcje do pracy z kodem. Bez jawnego wyboru modelu używa `9b`. |
| `model --q --think treść pytania` | Włącza tryb rozumowania modelu. |
| `model --q --fast --think treść pytania` | Łączy tryb rozumowania z deterministycznym generowaniem (`temperature=0`). |
| `model --q --no-mtp treść pytania` | Wyłącza MTP dla tego uruchomienia. |
| `model context` | Pokazuje licznik tur, ostatnie użycie kontekstu, liczbę kompresji i lokalizację archiwum. |
| `model compact` | Streszcza starszą część rozmowy; zachowuje dosłownie dwie ostatnie tury. |
| `model clear` | Czyści bieżącą sesję rozmowy. **Nie usuwa archiwum** `history.jsonl`. |
| `model logs` | Wyświetla ostatnie 80 wierszy logu serwera. |

Flagi `--4b` i `--9b` wykluczają się. Poza `--think` zwykły czat działa domyślnie w trybie `fast`; `--fast` bez `--think` nie zmienia tego zachowania. Pytanie może zawierać kilka słów, np. `model --q --9b Wyjaśnij działanie RAG`.

## Zmiana modeli

Konfiguracja znajduje się na początku [`model.py`](model.py), w słowniku `MODELS` pod kluczami `"4b"` i `"9b"`. Dla każdego wpisu można zmienić:

| Pole | Znaczenie |
| --- | --- |
| `label` | Pełna nazwa wyświetlana w interfejsie. |
| `short` | Krótka nazwa wyświetlana przy statystykach. |
| `target_rel` | Ścieżka do głównego modelu względem katalogu modeli LM Studio. |
| `draft_rel` | Ścieżka do modelu pomocniczego MTP względem tego samego katalogu. |

Przykład wpisu (podstaw własne **istniejące** katalogi modeli):

```python
"4b": {
    "label": "Mój model",
    "short": "MÓJ",
    "target_rel": "wydawca/nazwa-modelu",
    "draft_rel": "wydawca/nazwa-modelu-draft",
},
```

Skrypt szuka tych katalogów w `~/.lmstudio/models/` i `~/.lmstudio/hub/models/`. Jeśli ich tam nie znajdzie, przeszukuje `~/.lmstudio/` według końcowej nazwy katalogu. Katalog modelu musi zawierać `config.json`. Aby używać modeli przechowywanych gdzie indziej, zmień listę `candidates` w funkcji `resolve_model()`.

**Ważne ograniczenia obecnej wersji:**

- Są tylko dwa klucze wyboru (`4b` i `9b`). Po podmianie modeli flagi zachowają te nazwy, nawet jeśli nowy model ma inny rozmiar.
- Kod sprawdza obecność `draft_rel` również przy `--no-mtp`. W obecnej wersji katalog modelu pomocniczego musi więc istnieć niezależnie od tej flagi.
- Model główny i pomocniczy muszą być zgodne z używanym serwerem i trybem MTP. Sama zmiana ścieżki nie gwarantuje kompatybilności.
- Funkcja `api_endpoint()` używa `/chat/completions` dla klucza `4b`, a `/v1/chat/completions` dla `9b`. Jeśli nowy model wymaga innego endpointu, zmień tę funkcję.

Po zmianie konfiguracji użyj `model unload`, a następnie `model load --4b` lub `model load --9b`.

## Dane i prywatność

Skrypt zapisuje dane w `~/.local/share/model-cli/`:

| Plik | Zawartość |
| --- | --- |
| `session.json` | Bieżąca historia, streszczenie i statystyki. |
| `history.jsonl` | Archiwum pełnych pytań i odpowiedzi; `model clear` go nie czyści. |
| `state.json` | Identyfikator procesu i ścieżki aktywnych modeli. |
| `server.log` | Log serwera. |

Pytanie wpisane po `--q` może też zostać zapisane w historii powłoki i być widoczne jako argument uruchomionego procesu. Nie podawaj w ten sposób haseł ani tokenów. Serwer nasłuchuje na lokalnym adresie `127.0.0.1` i nie używa uwierzytelniania.
