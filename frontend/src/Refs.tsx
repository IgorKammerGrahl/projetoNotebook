// Text with [n] references rendered as links to cell n (D-019).
import { splitRefs } from "./status";

export function scrollToCell(id: number) {
  const el = document.getElementById(`cell-${id}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth;  // restart the animation
  el.classList.add("flash");
}

export function Refs({ text }: { text: string }) {
  return (
    <>
      {splitRefs(text).map((part, i) =>
        typeof part === "number"
          ? <button key={i} className="ref" onClick={() => scrollToCell(part)}>[{part}]</button>
          : <span key={i}>{part}</span>)}
    </>
  );
}
