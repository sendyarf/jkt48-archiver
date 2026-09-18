import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  // Plain <img> is intentional: thumbnails and avatars are served by third-party
  // CDNs (img.youtube.com, ui-avatars.com) with an onError fallback chain, and
  // next/image optimization requires sharp, which is not installed here.
  {
    files: ["app/**/*.tsx", "components/**/*.tsx"],
    rules: { "@next/next/no-img-element": "off" },
  },
  // Browser automation scripts in verify/ are plain Node scripts (CommonJS allowed).
  {
    files: ["verify/**/*.{cjs,mjs,js}"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
      "@typescript-eslint/no-unused-expressions": "off",
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
]);

export default eslintConfig;
