/* The unit-test config, kept apart from `vite.config.ts` on purpose: the
 * production image builds the bundle with `npm run build`, and nothing in
 * that path should have to resolve vitest to do it.
 *
 * The pure layers — thread state, the trail, the particle field, and the
 * two stream/snapshot reconcilers — are tested without a DOM. Component
 * tests would need jsdom; the bugs these layers produce do not. */
import { mergeConfig, defineConfig } from "vitest/config";
import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "node",
      include: ["src/**/*.test.ts"],
      reporters: "dot",
    },
  }),
);
