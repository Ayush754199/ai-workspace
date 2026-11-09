import React from "react";

/**
 * Range input that paints its filled track in the accent colour and can show
 * an optional gradient (used by the Temperature/Tint sliders).
 */
const StudioRange: React.FC<{
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  gradient?: string;
  accent?: string;
  label?: string;
}> = ({ value, min, max, step = 1, onChange, gradient, accent = "#8b5cf6", label }) => {
  const percent = ((value - min) / (max - min)) * 100;
  const track = gradient
    ? gradient
    : `linear-gradient(90deg, ${accent} 0%, ${accent} ${percent}%, #2a2d3a ${percent}%, #2a2d3a 100%)`;
  return (
    <input
      className="studio-range"
      type="range"
      aria-label={label}
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      style={{ background: track }}
    />
  );
};

export default StudioRange;
