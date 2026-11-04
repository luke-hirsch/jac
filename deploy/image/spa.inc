# The SPA: hashed bundles forever, index.html never, so a deploy shows up on the next load.
location /assets/ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
location / {
    try_files $uri /index.html;
    add_header Cache-Control "no-cache";
    add_header X-Robots-Tag $spa_robots always;
    # One image on any domain: the bundle reads this from a meta tag (lib/host.ts).
    sub_filter "__BASE_DOMAIN__" "@BASE_DOMAIN@";
    sub_filter_once off;
}
