# Changelog

## 26.11.2 – 02.11.2026

- **Fix:** a weather effect (storm, clouds) that was running when Home Assistant restarted never ended: it was saved
  as a lasting wish, its timer was lost, and "hold state" brought the lightning back again and again. The effect is
  no longer saved as the lasting state; its end time is kept, so after a restart it runs for the time left and then
  returns to the previous mix. Turned off or switched to another mode while it runs, the light stays that way when
  the effect ends; an effect the lamp did not take is not kept either.
- **Fix:** unloading the integration while a connection was being set up could leave the connection open on the
  Bluetooth proxy (lamp busy until the proxy restarts). A connection that completes after the stop is now closed,
  and reconnects or idle disconnects started in the background are finished before unloading completes.

## 26.11.1 – 01.11.2026

- **Profiles:** save professional programs as named profiles in Home Assistant and switch between them.
  The FluvalConnect app keeps its profiles on the phone and the lamp only stores the active program, so the app's
  list cannot be read. New button `save_profile` saves the program the lamp holds right now (pick a profile in the
  app, press the button); new select `profile` writes a saved profile into the lamp and switches to professional.
  Up to 20 profiles, kept in the entry options. Entries the integration cannot read are kept as they are.
- Options: once profiles exist, the options open a menu: settings, or rename/delete a profile.
- Reef Nano 4.0: no change to existing unique IDs, entity IDs or behaviour.
- Going back to 26.10.4 keeps the profiles in the entry, but saving the options there removes them.

## 26.10.4 – 22.10.2026

- **Fix:** after a long outage (lamp unreachable for many polls in a row) the retry backoff overflowed
  (`OverflowError` in the coordinator, logged as "Unexpected error fetching fluvalble data"). The backoff now stays
  at 5 minutes however long the lamp is away, and it comes back normally once the lamp answers again.

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
