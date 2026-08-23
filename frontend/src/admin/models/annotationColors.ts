const FALLBACK_COLORS = ["#8b5cf6", "#f59e0b", "#06b6d4", "#ec4899", "#84cc16", "#f97316"];

export function annotationColor(classId: number, classes: string[]): string {
  const name = classes[classId]?.trim().toLowerCase();
  if (name === "person") return "#2f80ed";
  if (name === "helmet") return "#20a464";
  if (name === "no_helmet") return "#ef4444";
  return FALLBACK_COLORS[Math.abs(classId) % FALLBACK_COLORS.length];
}
