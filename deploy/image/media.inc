# Public uploads: avatars, portfolio images, a block's audio or video, layout JSON. The second wall
# behind the upload validation: only whitelisted types get a real Content-Type, nothing is sniffed,
# and anything that does render as a page runs sandboxed — no script, no origin. Range requests work
# out of the box, which seeking needs (and Safari, to play at all).
alias /files/media/;
types {
    image/png png;
    image/jpeg jpg jpeg;
    image/gif gif;
    image/webp webp;
    image/avif avif;
    video/mp4 mp4;
    video/webm webm;
    audio/mpeg mp3;
    audio/mp4 m4a;
    audio/ogg ogg;
    application/json json;
}
default_type application/octet-stream;
add_header X-Content-Type-Options nosniff always;
# media-src: a file opened on its own plays in the browser's own player page.
add_header Content-Security-Policy "default-src 'none'; img-src 'self'; media-src 'self'; sandbox" always;
expires 1h;
