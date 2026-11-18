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
| [04-milestone-m2-m4.md](04-milestone-m2-m4.md) | Границы (M2), выборы и партии (M3), назначения в правительстве (M4) |
| **[05-build-and-install.md](05-build-and-install.md)** | **Сборка и установка от А до Я: `install.bat`, что скачать, как собрать DLL, куда положить, что делать при ошибках** |
| [map-preview.png](map-preview.png) | Превью разбиения карты ( RSA — синий, ФЗСА — оранжевый, спорные — жёлтый) |

## Инструменты (`tools/`)

```bash
python3 tools/build_regions.py      # пересобрать Regions.xml + превью карты и проверить геометрию
python3 tools/sim_reference.py --years 2   # исполняемая эталонная модель механик
python3 tools/check_parity.py       # не разошлись ли C# и эталонная модель (244 проверки)
python3 tools/test_world_data.py    # тесты контракта данных и сохранений (832 проверки)
python3 tools/check_sources.py      # структурная проверка C# без компилятора
```

## Быстрый старт

**В репозитории только исходники — готового DLL нет.** Полная инструкция со
всеми ошибками — в **[05-build-and-install.md](05-build-and-install.md)**.

### Автоматически (Windows)

Двойной клик по **`install.bat`** в корне репозитория. Он сам найдёт папку GTA V,
скопирует `ScriptHookVDotNet3.dll` в `lib\shvdn3\`, соберёт проект (MSBuild, а
если его нет — компилятором `csc.exe` из .NET Framework, поэтому **Visual Studio
не обязателен**), разложит файлы и проверит результат. `uninstall.bat` — снятие,
сохранения не трогает. Подробности — в разделе «Быстрый путь: install.bat»
документа 05.

### Вручную

1. Положить `ScriptHookVDotNet3.dll` (Legacy, 3.6/3.7) в `lib/shvdn3/`.
2. Открыть `src/OnyxNations/OnyxNations.csproj` в Visual Studio, собрать Release.
3. `OnyxNations.dll` → в `<GTA>\scripts\`, `OnyxNations.ini` → туда же,
   папку `OnyxNations/` → в корень игры (рядом с `ProjectOnyx/`).
4. В игре: `F6` — планшет (7 экранов, переключение `←` `→`, действие `Enter`),
   `NumPad1/2` — передать регион государству (dev), `NumPad7/8` — война / мир,
   `NumPad9` — приказ частям, `NumPad0` — сохранить.
5. Проверить `<GTA>\OnyxNations\onyx-nations.log`: строка `ready: 2 nations, 14 regions`
   означает, что всё работает.

Подробности и честный список ограничений — в [02-milestone-m0-m1.md](02-milestone-m0-m1.md).
