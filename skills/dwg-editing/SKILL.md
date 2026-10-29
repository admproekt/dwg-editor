---
name: dwg-editing
description: Use when the user wants to open, inspect, preview or edit an AutoCAD drawing (.dwg or .dxf) — list layers/entities, add or change lines, circles, text, dimensions, replace text in a title block, then save back to DWG. Также для запросов «открой чертёж», «измени слой», «добавь размер», «замени текст в штампе».
metadata:
  kind: general
  drawings: [any]
  tools: [dwg-editor]
  status: draft
  version: 0.1.0
  origin: dwg-editor
---

# Работа с чертежами DWG/DXF

Для работы есть MCP-инструменты сервера `dwg-editor`. Порядок работы:

1. **Открыть**: `open_drawing(path)`. Для `.dwg` нужен ODA File Converter; если инструмент вернул ошибку про него, дайте пользователю ссылку из сообщения и остановитесь.
2. **Разобраться в чертеже** до любых правок: `drawing_info`, `list_layers`, `list_entities` (с фильтрами по слою и типу), `render_preview` и затем `Read` на полученный PNG, чтобы увидеть чертёж глазами.
3. **Править** точечно: `add_*`, `modify_entity`, `move_entities`, `delete_entities`, `replace_text`. Объекты адресуются по `handle`, который возвращают `list_entities` и `add_*`. Есть также `copy_entities`, `rotate_entities`, `scale_entities`, `mirror_entities`, свойства слоя (`set_layer_properties`, `delete_layer`) и пакетные `modify_entities` и `move_entities_each` для десятков объектов за один вызов. Положение текста с выравниванием не по левому краю задаёт `align_point`, поэтому сдвигайте текст через `move_entities` или `move_entities_each`, а не через `insert` в `modify_entity`.
4. **Проверить результат**: снова `render_preview` и показать пользователю, что изменилось.
5. **Сохранить**: `save_drawing`. По умолчанию перезаписывается исходный файл (для DWG рядом создаётся `.bak`). Если пользователь не просил менять оригинал, сохраняйте под новым именем через `output_path`.

## Правила

- Удаление и массовые изменения (`delete_entities`, `delete_layer`, `replace_text` без слоя, `move_entities` и пакетные инструменты для многих объектов): сначала покажите, что именно будет затронуто, и дождитесь подтверждения.
- Координаты и размеры даются в единицах чертежа (смотрите `units` в `drawing_info`). Не угадывайте масштаб: уточните у пользователя, если он называет размеры в других единицах.
- Углы в градусах, против часовой стрелки. Цвет — индекс AutoCAD (ACI): 1 красный, 2 жёлтый, 3 зелёный, 4 голубой, 5 синий, 6 пурпурный, 7 белый/чёрный.
- Динамические блоки, прокси-объекты и сложные внешние ссылки при конвертации DWG→DXF→DWG могут потерять часть свойств. Предупредите об этом, если такие объекты есть в чертеже.
- `close_drawing` с несохранёнными правками требует явного `discard_changes=true`; не используйте его без согласия пользователя.
