import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 32,
          height: 32,
          background: "#000",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 6,
        }}
      >
        <span
          style={{
            color: "#ccff00",
            fontSize: 22,
            fontWeight: 700,
            fontFamily: "serif",
            lineHeight: 1,
            marginTop: 1,
          }}
        >
          S
        </span>
      </div>
    ),
    { ...size }
  );
}
