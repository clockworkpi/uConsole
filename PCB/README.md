# Hardware file inventory

The two archives in this directory contain PCB manufacturing exports. They
are not editable KiCad, EasyEDA, or other native CAD projects. Opening them
as schematic/project files will fail. Extract them first and use a Gerber
viewer or your manufacturer's Gerber import workflow.

## Mainboard v3.14 V5

`CPI_3.14_Mainboard_V5_Gerber.7z` contains 19 files in these directories:

- `V3FLC003D0_0828_1202A_CAM`: copper, silkscreen, solder-mask and drill-drawing
  artwork (`.art`), plus an aperture report.
- `V3FLC003D0_0828_1202A_ASM`: assembly artwork and aperture report.
- `V3FLC003D0_0828_1202A_SMT`: solder-paste artwork, aperture report, and
  **`smt_loc.txt` component placement data**.

The `.art` files identify themselves as Gerber RS-274X. The placement export
declares `VERSION = 2.0` and `UUNITS = MILS`, with reference designator, X/Y
position, rotation, mirror, symbol name and embedded-layer columns. It is an
existing source for placement information requested in
[issue #47](https://github.com/clockworkpi/uConsole/issues/47), but is not a
manufacturer-specific CPL CSV. Any conversion must verify units, origin,
rotation and bottom-side conventions with the manufacturer. It does not
provide a purchasing BOM with component values and manufacturer part numbers.

To extract the placement report without unpacking the whole archive:

```sh
7z x -so CPI_3.14_Mainboard_V5_Gerber.7z \
  CPI_3.14_Mainboard_V5_Gerber/V3FLC003D0_0828_1202A_SMT/smt_loc.txt \
  > smt_loc.txt
```

The mainboard archive includes a drill drawing (`drill-1-6.art`); a separate
Excellon drill file is not present. Archive integrity tests pass, but that
does not qualify the files as a complete fabrication/assembly package.

## CM4 adapter

`RPI CM4 to CPI v3.14 Adapter.zip` contains 11 files under the directory
`PRI CM4 to CPI v3.14 Adapter` (the directory spelling is from the archive):
copper, silkscreen, solder-mask and drill artwork, one `.drl` file, and one
`.ipc` file. The `.art` headers identify RS-274X Gerber. No BOM or component
placement report is included in this archive.

## Sources still needed

The checkout contains PDF schematics and assembly documents at its root,
but no native editable PCB/schematic project, mechanical STEP/STL model,
or purchasing BOM was found in the repository or these two PCB archives.
The 4G modem and battery-board editable design files requested in issues
#31 and #32 are also absent. These need to be supplied by the design owner;
renaming or converting Gerbers does not recover schematic connectivity,
component libraries, manufacturing intent, or a reliable bill of materials.

No repository-wide hardware license file was found. Obtain the applicable
hardware license from the owner before assuming rights beyond those stated
in individual files. This inventory records availability, not a legal
determination of open-source hardware compliance.
