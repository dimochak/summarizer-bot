# Картинки для реакції на Кличка

Підтримуються `.jpg`, `.jpeg`, `.png`, `.webp`, а також анімації `.gif` і `.mp4`.
Бот обирає випадкову картинку, коли в чаті згадують Віталія Кличка.

**Картинки в репозиторій не потрапляють**: теку ігнорують і `.gitignore`, і
`.dockerignore`. Під git лишається лише цей README.

## Локально

Поклади картинки сюди, у `assets/reactions/klitschko/`.

## У проді

Картинки лежать на томі Fly у `/app/data/reactions/klitschko/`, шлях задає
`REACTIONS_DIR` у `fly.toml`. Залити їх можна так:

    fly ssh console -C "mkdir -p /app/data/reactions/klitschko"
    fly ssh sftp shell
    » put ./klitschko-1.jpg /app/data/reactions/klitschko/klitschko-1.jpg

Перезапускати бота не треба: теку перечитують при кожній реакції. На старті
бот пише в лог, скільки картинок знайшов.
