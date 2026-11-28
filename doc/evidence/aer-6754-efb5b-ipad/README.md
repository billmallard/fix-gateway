# EFB5b iPad check (AER-6754)

Bill's photos, 2026-11-28 00:29-00:30Z (issue AER-6754, attachments IMG_3660, 3661 and 3663).
They are downscaled to 1600 px on the long side, EXIF-rotated, at JPEG quality 85.

The Beelink ran fix-gateway 1a25926 (PR #269) with `assumed_accuracy_ft: 16`. Nothing wrote
`GPS_ACCURACY_HORIZ` or `GPS_ACCURACY_VERTICAL`, and X-Plane was the position source.

- `ipad-beside-pyefis.jpg`: ForeFlight beside pyEfis. pyEfis shows IAS 105, altitude about
  7,9xx ft, VSI 0, and GS 93 / TAS 119 on the map.
- `ipad-attitude-and-map.jpg`: ForeFlight shows "GS KTS" 93, "GPS ALT" 7,910 and VS 0, and
  ownship over the map.
- `ipad-speed-alt-vs-closeup.jpg`: the same tapes, closer.

A wire decode at the same time, from the bench's ForeFlight discovery listener, gave:
- 0x0B: 7,905 ft, VFOM 5 m (`0b062d0005`).
- 0x0A: PALT 7,900, VS 0, GS 93, NACp 10.
- 0x65/1: IAS 105, TAS 119.

Regenerate:
`python3 -c "from PIL import Image,ImageOps; im=ImageOps.exif_transpose(Image.open('IMG_3660.jpeg')); im.thumbnail((1600,1600)); im.save('ipad-beside-pyefis.jpg',quality=85)"`
