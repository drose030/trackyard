// electron-builder afterPack: sign the Mac app ad hoc. Without a valid signature macOS
// calls a downloaded app "damaged" and only offers the bin; signed ad hoc, the user can
// allow it under Privacy & Security. No certificate is involved (electron-builder's own
// "-" identity would pick up a developer certificate from the keychain).
const { execFileSync } = require("child_process");
const path = require("path");

exports.default = async function adhocSign(context) {
  if (context.electronPlatformName !== "darwin") return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", app], { stdio: "inherit" });
  execFileSync("codesign", ["--verify", "--deep", "--strict", app], { stdio: "inherit" });
};
