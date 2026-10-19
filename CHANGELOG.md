# Changelog

## 26.10.3 – 19.10.2026

- **More lights:** besides the Reef Nano 4.0 the integration now supports every Fluval light of the current
  Bluetooth generation with five channels on the FFF0 service: Reef 4.0 (546), A-Sky Aqua 1025mm (385),
  Plant 4.0 (545), Plant Nano 4.0 (548), Plant PRO (386) and Siena 2.0 (563). The model is detected from the product
  ID in the Bluetooth advertisement and shown on the device page.
- Plant models get their own channels (Pink, Blue, Cold white, Pure white, Warm white) with matching colour
  conversion from the Plant spectrum; `set_channels` and `set_pro_program` accept the channel names of the model.
- Discovery also accepts advertisements with the company ID bytes `FF 01`.
- Reef Nano 4.0: no change. Same unique IDs, entity IDs, channel names and behaviour.
- Not supported (unchanged): the old encrypted generation (Plant 3.0, Marine 3.0, AquaSky 2.0, Wing Nano …),
  AquaSky 3.0 and Roma & Shaker 2.0. Use MrMooreUK/fluvalble for those.

## 26.10.2 – 19.10.2026

- Signal strength updates throttled; errors in confirmation replies are caught.

## 26.10.1 – 16.10.2026

- New calendar version numbers (year.month.release in that month).
