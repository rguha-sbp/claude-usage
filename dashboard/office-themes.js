/* Office floor themes (spec Sec 15.3). Pure data: no DOM, no state, so
   tests/office_themes_check.js can check that every theme is complete.

   A theme is everything about the floor that is a matter of taste:
   - `palette`: every colour the scenery reads, by role. Furniture asks for a
     role ("wood", "leaf"), never for a literal colour, so a theme swap
     recolours the whole floor.
   - `floor`: the pattern office-3d.js paints on the floor canvas.
   - `partition`: the style of the low walls around each neighbourhood.
   - `decor`: which fill pieces office-floor.js may place and how often
     (weights), how full the free floor gets (density, 0..1), and the
     centrepiece for a big open patch.
   - `light`: the mood. The viewer's clock (OfficeCast.skyOf) still sets
     day or night on top of it.
   Desk shirts, hair and skin are not themed: people look the same in
   every office. */
window.OfficeThemes = (function () {
  'use strict';

  var THEMES = [
    {
      key: 'studio', label: 'Studio',
      palette: {
        floor: '#e8dcc6', floorLine: '#d8c6a6', zone: '#f3ede2', carpet: '#cfdccb', tiles: '#e9eef0',
        wall: '#f6f3ee', trim: '#ddd5c9', glass: '#cfe7ef', frame: '#fbfbfa',
        desk: '#f1e6d2', deskLeg: '#cfc7ba', wood: '#d9b88a', woodDark: '#b58f63',
        metal: '#b8bec5', dark: '#3a4047', screen: '#5aa9ff',
        fabric: '#9cb8a6', fabric2: '#e3c9a6', cushion: '#f0e3cf',
        accent: '#e07a5f', accent2: '#3d8b8b', accent3: '#f2cc8f',
        leaf: '#5c9a4c', leaf2: '#86b85a', pot: '#f4f1ea', soil: '#7a5c45',
        book1: '#e07a5f', book2: '#3d8b8b', book3: '#f2cc8f', paper: '#fbfaf7', bulb: '#fff2c8'
      },
      floor: { pattern: 'planks', size: 0.22 },
      partition: 'planter',
      decor: {
        density: 0.85, centrepiece: 'pingpong',
        weights: { plant: 5, tallPlant: 4, shelf: 2, bookcase: 2, floorLamp: 2, beanbag: 2, bench: 1,
                   armchair: 2, sideTable: 1, umbrella: 1, bin: 1, planter: 3, printer: 1 }
      },
      light: { sky: '#ffffff', ground: '#e6dccb', hemi: 1.9, sun: '#fff6e8', sunI: 2.1, warm: 0.0 }
    },
    {
      key: 'loft', label: 'Loft',
      palette: {
        floor: '#9a9893', floorLine: '#86837d', zone: '#a9a49b', carpet: '#7c5a48', tiles: '#5e6166',
        wall: '#a55a42', trim: '#7d3f2e', glass: '#b9cdd4', frame: '#2d2f33',
        desk: '#6b4a34', deskLeg: '#2d2f33', wood: '#7a5238', woodDark: '#4e3324',
        metal: '#5e646b', dark: '#25282c', screen: '#7fc4ff',
        fabric: '#8a5a3c', fabric2: '#3f4a52', cushion: '#c9a77c',
        accent: '#d98e32', accent2: '#4f7f8c', accent3: '#c4b59a',
        leaf: '#4f7d43', leaf2: '#6f9a4a', pot: '#3a3d42', soil: '#4a3628',
        book1: '#a8432f', book2: '#d98e32', book3: '#4f7f8c', paper: '#efe8da', bulb: '#ffc46b'
      },
      floor: { pattern: 'concrete', size: 3.0 },
      partition: 'brick',
      decor: {
        density: 0.8, centrepiece: 'pingpong',
        weights: { plant: 3, tallPlant: 2, shelf: 3, bookcase: 2, floorLamp: 3, armchair: 3, sideTable: 2,
                   crate: 3, bike: 1, umbrella: 1, bin: 1, printer: 1, bench: 2 }
      },
      light: { sky: '#fff1dc', ground: '#5a4636', hemi: 1.55, sun: '#ffdcae', sunI: 1.8, warm: 0.6 }
    },
    {
      key: 'greenhouse', label: 'Greenhouse',
      palette: {
        floor: '#c98a64', floorLine: '#b0714f', zone: '#d9a07c', carpet: '#9db38a', tiles: '#e7dfcf',
        wall: '#eef1ea', trim: '#c9d3c4', glass: '#d6efe6', frame: '#557a5c',
        desk: '#ece4d4', deskLeg: '#557a5c', wood: '#c49a6c', woodDark: '#8c6a47',
        metal: '#9fb1a5', dark: '#2f3d35', screen: '#6fd0b0',
        fabric: '#6f9a72', fabric2: '#e6cfa3', cushion: '#f1e8d6',
        accent: '#c4553d', accent2: '#e2a93b', accent3: '#9b6fa8',
        leaf: '#3f8a46', leaf2: '#78b04f', pot: '#c26a45', soil: '#5b4031',
        book1: '#c4553d', book2: '#557a5c', book3: '#e2a93b', paper: '#f7f3ea', bulb: '#fff0c4'
      },
      floor: { pattern: 'terracotta', size: 0.5 },
      partition: 'vines',
      decor: {
        density: 1.0, centrepiece: 'bed',
        weights: { plant: 6, tallPlant: 6, palm: 4, planter: 4, cactus: 2, trellis: 3, bench: 2,
                   armchair: 1, sideTable: 1, shelf: 1, umbrella: 1, bin: 1, printer: 1 }
      },
      light: { sky: '#f4fff6', ground: '#b98a64', hemi: 2.0, sun: '#fffbe6', sunI: 2.3, warm: 0.15 }
    }
  ];

  /* The keys every theme must define: the first theme is the reference. */
  var PALETTE_KEYS = Object.keys(THEMES[0].palette);

  function themeOf(key) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].key === key) return THEMES[i];
    return THEMES[0];
  }

  return { THEMES: THEMES, PALETTE_KEYS: PALETTE_KEYS, themeOf: themeOf };
})();
