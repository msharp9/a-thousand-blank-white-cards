/** True when dev-only UI (debug panels, inspectors) should render. */
export function isDevMode(): boolean {
  return (
    process.env.NEXT_PUBLIC_DEV_MODE === "true" ||
    process.env.NODE_ENV === "development"
  );
}
