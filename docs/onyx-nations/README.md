# ONYX RP: NATIONS & WAR — документация расширения

Расширение собирается как **отдельный companion-скрипт ScriptHookVDotNet v3**
для GTA V Legacy (Story Mode) и работает рядом с Onyx RP, не вмешиваясь в него.

## Документы

| Файл | О чём |
|---|---|
| [01-technical-audit.md](01-technical-audit.md) | Технический аудит Onyx RP: что есть, что можно расширить, ограничения GTA V, план из 8 milestone |
| [00-authorization.md](00-authorization.md) | Запись заявления правообладателя и границы, которые остаются в силе |
| [02-milestone-m0-m1.md](02-milestone-m0-m1.md) | Что сделано в M0–M1, как собрать, как проверить в игре, чего там нет |
| [03-milestone-m2-m7.md](03-milestone-m2-m7.md) | Ядро симуляции M2–M7: армии, бой, оккупация, экономика, мозг ИИ, сохранения, планшет |
| [map-preview.png](map-preview.png) | Превью разбиения карты ( RSA — синий, ФЗСА — оранжевый, спорные — жёлтый) |

## Инструменты (`tools/`)

```bash
python3 tools/build_regions.py      # пересобрать Regions.xml + превью карты и проверить геометрию
python3 tools/sim_reference.py --years 2   # исполняемая эталонная модель механик
python3 tools/check_parity.py       # не разошлись ли C# и эталонная модель (165 проверок)
python3 tools/test_world_data.py    # тесты контракта данных и сохранений (391 проверка)
python3 tools/check_sources.py      # структурная проверка C# без компилятора
```

## Быстрый старт для сборки

1. Положить `ScriptHookVDotNet3.dll` (Legacy, 3.6/3.7) в `lib/shvdn3/`.
2. Открыть `src/OnyxNations/OnyxNations.csproj` в Visual Studio, собрать Release.
3. `OnyxNations.dll` → в `<GTA>\scripts\`, `OnyxNations.ini` → туда же,
   папку `OnyxNations/` → в корень игры (рядом с `ProjectOnyx/`).
4. В игре: `F6` — планшет (сводка / армия / МИД / бюджет, переключение `←` `→`,
   действие `Enter`), `NumPad1/2` — передать регион государству (dev),
   `NumPad7/8` — война / мир (dev), `NumPad9` — приказ частям (dev),
   `NumPad0` — сохранить.

Подробности и честный список ограничений — в [02-milestone-m0-m1.md](02-milestone-m0-m1.md).
