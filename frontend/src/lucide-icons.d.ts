// Per-icon imports (review decision 2): lucide-react ships each icon as its own ESM
// module but without per-file types.
declare module "lucide-react/dist/esm/icons/*.mjs" {
  import type { LucideIcon } from "lucide-react";
  const Icon: LucideIcon;
  export default Icon;
}
