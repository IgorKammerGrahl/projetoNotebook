import Ban from "lucide-react/dist/esm/icons/ban.mjs";
import Braces from "lucide-react/dist/esm/icons/braces.mjs";
import CircleAlert from "lucide-react/dist/esm/icons/circle-alert.mjs";
import CircleX from "lucide-react/dist/esm/icons/circle-x.mjs";
import Cog from "lucide-react/dist/esm/icons/cog.mjs";
import Copy from "lucide-react/dist/esm/icons/copy.mjs";
import GitBranch from "lucide-react/dist/esm/icons/git-branch.mjs";
import Hammer from "lucide-react/dist/esm/icons/hammer.mjs";
import Hourglass from "lucide-react/dist/esm/icons/hourglass.mjs";
import ListOrdered from "lucide-react/dist/esm/icons/list-ordered.mjs";
import LoaderCircle from "lucide-react/dist/esm/icons/loader-circle.mjs";
import PencilLine from "lucide-react/dist/esm/icons/pencil-line.mjs";
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
import Square from "lucide-react/dist/esm/icons/square.mjs";
import Zap from "lucide-react/dist/esm/icons/zap.mjs";
import type { Icon } from "./status";

export { default as Play } from "lucide-react/dist/esm/icons/play.mjs";
export { default as Plus } from "lucide-react/dist/esm/icons/plus.mjs";
export { default as Skull } from "lucide-react/dist/esm/icons/skull.mjs";
export { default as Trash } from "lucide-react/dist/esm/icons/trash-2.mjs";
export { default as Network } from "lucide-react/dist/esm/icons/network.mjs";
export { default as WifiOff } from "lucide-react/dist/esm/icons/wifi-off.mjs";
export { default as RotateCcw } from "lucide-react/dist/esm/icons/rotate-ccw.mjs";
export { Square };

const MAP = {
  "list-ordered": ListOrdered, hourglass: Hourglass, hammer: Hammer, loader: LoaderCircle,
  pencil: PencilLine, "circle-x": CircleX, braces: Braces, copy: Copy, refresh: RefreshCw,
  ban: Ban, zap: Zap, square: Square, "git-branch": GitBranch, cog: Cog, alert: CircleAlert,
} satisfies Record<Icon, unknown>;

export function StateIcon({ name, spin }: { name: Icon; spin?: boolean }) {
  const C = MAP[name];
  return <C size={14} aria-hidden="true" className={spin ? "spin" : undefined} />;
}
