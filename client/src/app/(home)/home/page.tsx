"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import OpenPaperLanding from "@/components/OpenPaperLanding";

export default function HomePage() {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (localStorage.getItem("auth_user")) {
      router.replace("/");
    } else {
      setChecked(true);
    }
  }, [router]);

  if (!checked) return null;
  return <OpenPaperLanding />;
}
