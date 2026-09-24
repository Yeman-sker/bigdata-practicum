import { useEffect, useRef } from "react";
import { animate, useReducedMotion } from "motion/react";
import { easeOut } from "./motion";

/** Integer that rolls from its previous value; the final text is always exact. */
export function AnimatedNumber({ value }: { value: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const previous = useRef(value);
  const reduce = useReducedMotion();
  useEffect(() => {
    const node = ref.current;
    const from = previous.current;
    previous.current = value;
    if (!node) return;
    // Write into React's own text node so later renders keep updating it.
    const write = (text: string) => {
      if (node.firstChild) node.firstChild.nodeValue = text;
      else node.textContent = text;
    };
    if (reduce || from === value) {
      write(String(value));
      return;
    }
    const controls = animate(from, value, {
      duration: 0.55,
      ease: easeOut,
      onUpdate: (latest) => {
        write(String(Math.round(latest)));
      },
      onComplete: () => write(String(value)),
    });
    return () => {
      controls.stop();
      write(String(value));
    };
  }, [value, reduce]);
  return <span ref={ref}>{value}</span>;
}
