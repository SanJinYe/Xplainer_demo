import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
    plugins: [react()],
    build: {
        outDir: "dist",
        emptyOutDir: true,
        assetsDir: "assets",
        cssCodeSplit: false,
        rollupOptions: {
            input: path.resolve(__dirname, "index.html"),
            output: {
                entryFileNames: "assets/index.js",
                chunkFileNames: "assets/[name].js",
                assetFileNames: (assetInfo) => {
                    if (assetInfo.name?.endsWith(".css")) {
                        return "assets/index.css";
                    }
                    return "assets/[name][extname]";
                },
            },
        },
    },
});
