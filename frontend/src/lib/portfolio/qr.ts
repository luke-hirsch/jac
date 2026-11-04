import QRCode from "qrcode";

/** Raster QR for react-pdf — <Image> can't take the qrcode.react SVG component.
 *  512px source drawn at ~18mm keeps modules crisp on print. */
export function qrDataUrl(url: string): Promise<string> {
  return QRCode.toDataURL(url, { margin: 0, width: 512 });
}
