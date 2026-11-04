# Public uploads: avatars, portfolio images, layout JSON. The second wall behind the upload
# validation: only whitelisted types get a real Content-Type, nothing is sniffed, and anything that
# does render as a page runs sandboxed — no script, no origin.
alias /files/media/;
types {
    image/png png;
    image/jpeg jpg jpeg;
    image/gif gif;
    image/webp webp;
    image/avif avif;
    application/json json;
}
default_type application/octet-stream;
add_header X-Content-Type-Options nosniff always;
add_header Content-Security-Policy "default-src 'none'; img-src 'self'; sandbox" always;
expires 1h;
