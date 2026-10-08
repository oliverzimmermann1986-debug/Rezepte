#!/usr/bin/env node
"use strict";

// Technical exports only: preserve the source artwork, scale it and add the
// maskable safety margin. No baked-in corner mask or transparent pixels.
const fs = require("node:fs/promises");
const path = require("node:path");
const sharp = require(process.env.REZEPTE_SHARP_MODULE || "sharp");

const ROOT = path.resolve(__dirname, "..");
const MASTER = path.join(ROOT, "assets/branding/app-icon-master.png");
const BACKGROUND = "#191813";
const MASKABLE_CONTENT_SIZE = 430;

async function main() {
  if (sharp.versions.sharp !== "0.35.4") {
    throw new Error("Use sharp 0.35.4 for reproducible icon exports.");
  }
  const metadata = await sharp(MASTER).metadata();
  if (metadata.width !== metadata.height || metadata.width < 1024) {
    throw new Error("The icon master must be square and at least 1024 pixels wide.");
  }

  const exportPNG = (size) => sharp(MASTER)
    .resize(size, size, { kernel: "lanczos3" })
    .flatten({ background: BACKGROUND })
    .toColourspace("srgb")
    .removeAlpha()
    .png({ compressionLevel: 9, adaptiveFiltering: false, palette: false });

  const icon1024 = await exportPNG(1024).toBuffer();
  const outputs = [
    ["ios-swift/Rezepte/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png", icon1024],
    ["native-ios/assets/images/icon.png", icon1024],
    ["native-ios/assets/images/splash-icon.png", icon1024],
    ["app/static/icon-192.png", await exportPNG(192).toBuffer()],
    ["app/static/icon-512.png", await exportPNG(512).toBuffer()],
    ["app/static/icon-maskable.png", await exportPNG(MASKABLE_CONTENT_SIZE)
      .extend({ top: 41, bottom: 41, left: 41, right: 41, background: BACKGROUND })
      .toBuffer()],
  ];
  for (const [relativePath, contents] of outputs) {
    const exported = await sharp(contents).metadata();
    if (exported.channels !== 3 || exported.hasAlpha || exported.space !== "srgb") {
      throw new Error(`Expected opaque RGB export: ${relativePath}`);
    }
    await fs.writeFile(path.join(ROOT, relativePath), contents);
    console.log(`${relativePath}: ${exported.width} x ${exported.height}, RGB`);
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
