/** The commit this bundle was built from. See `build.d.ts`. */
export const BUILD_SHA: string = __BUILD_SHA__;

let announced = false;

/** Log the build once per page load, so a screenshot of the console dates it. */
export function announceBuild(): void {
  if (announced) return;
  announced = true;
  // eslint-disable-next-line no-console
  console.info(`SYNAPSE ui build ${BUILD_SHA}`);
}
