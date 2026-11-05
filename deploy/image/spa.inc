# The SPA: hashed bundles forever, index.html never, so a deploy shows up on the next load.
location /assets/ {
    expires 1y;
    add_header Cache-Control "public, immutable";
    add_header X-Content-Type-Options nosniff always;
}
location / {
    try_files $uri /index.html;
    add_header Cache-Control "no-cache";
    add_header X-Robots-Tag $spa_robots always;
    # Django sets these on what it serves; nginx answers this one itself. No CSP yet: the inline
    # script in index.html needs a hash first.
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    # One image on any domain: the bundle reads this from a meta tag (lib/host.ts).
    sub_filter "__BASE_DOMAIN__" "@BASE_DOMAIN@";
    sub_filter_once off;
}
