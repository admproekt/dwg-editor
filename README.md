# dwg-editor — плагин Claude Code для чертежей DWG/DXF

Открывает, показывает и редактирует чертежи AutoCAD прямо из Claude Code.

## Как это работает

DWG — закрытый формат. Плагин конвертирует `DWG → DXF` через **ODA File Converter**, редактирует DXF библиотекой **ezdxf** и при сохранении конвертирует обратно `DXF → DWG`. Файлы `.dxf` открываются напрямую, без конвертера.

## Требования

1. **Python 3.10+** (на Windows: `winget install Python.Python.3.12`). Заглушка `python.exe` из Microsoft Store не подходит.
2. **ODA File Converter** (бесплатно, нужна регистрация): https://www.opendesign.com/guestfiles/oda_file_converter. Нужен только для `.dwg`.
   Если установлен не в стандартную папку, задайте переменную среды `ODA_FILE_CONVERTER` с путём к `ODAFileConverter.exe`.

Зависимости Python (`mcp`, `ezdxf`, `matplotlib`) устанавливаются автоматически в отдельное окружение при первом запуске.

## Установка

Из GitHub (маркетплейс `dwg-tools` лежит в самом репозитории):

```bash
claude plugin marketplace add admproekt/dwg-editor
claude plugin install dwg-editor@dwg-tools
```

В десктопном приложении Claude Code: **+ → Plugins → Add plugin**, плагин `dwg-editor` из маркетплейса `dwg-tools`.
Если маркетплейса в списке нет, добавьте его в `~/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "dwg-tools": { "source": { "source": "github", "repo": "admproekt/dwg-editor" } }
  },
  "enabledPlugins": { "dwg-editor@dwg-tools": true }
}
```

Для разработки, из папки проекта:

```bash
claude --plugin-dir .
```

После установки Python или ODA File Converter перезапустите приложение, чтобы оно увидело обновлённый PATH.

## Использование

- `/dwg путь\к\чертежу.dwg` — открыть и показать содержимое.
- Или просто просите: «открой plan.dwg, перенеси слой "Мебель" в красный цвет», «замени в штампе "Иванов" на "Петров" и сохрани как plan_v2.dwg».

## Инструменты MCP

| Группа | Инструменты |
| --- | --- |
| Файлы | `open_drawing`, `new_drawing`, `save_drawing`, `close_drawing` |
| Просмотр | `drawing_info`, `list_layers`, `list_entities`, `get_entity`, `render_preview` |
| Создание | `add_layer`, `add_line`, `add_circle`, `add_arc`, `add_polyline`, `add_text`, `add_mtext`, `add_linear_dimension` |
| Правка | `modify_entity`, `move_entities`, `delete_entities`, `replace_text` |

## Ограничения

- Работает с пространством модели. Листы (layouts) и видовые экраны не редактируются.
- При круговой конвертации DWG→DXF→DWG динамические блоки и прокси-объекты сторонних приложений могут потерять часть свойств. Перед перезаписью DWG создаётся копия `.bak`.
