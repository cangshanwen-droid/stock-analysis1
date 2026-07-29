"use client";

import { useEffect, useState } from "react";

export function ClockDisplay() {
  const [clockStr, setClockStr] = useState(() =>
    new Date().toLocaleTimeString("zh-CN", { hour12: false })
  );

  useEffect(() => {
    const t = setInterval(
      () =>
        setClockStr(
          new Date().toLocaleTimeString("zh-CN", { hour12: false })
        ),
      1000
    );
    return () => clearInterval(t);
  }, []);

  return <>{clockStr}</>;
}
