import type { Metadata } from "next";
import { Be_Vietnam_Pro, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import "./themes.css";
import "./dashboard.css";
import "./glass.css";
import "./auth.css";
import "./session-shell.css";
import "./upload-session.css";
import "./audit.css";
import "./reference-layout.css";

const beVietnamPro = Be_Vietnam_Pro({
  weight: ["400", "500", "600", "700"],
  subsets: ["latin", "vietnamese"],
  display: "swap",
  variable: "--font-sans",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "vietnamese"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "MasterOne — Quản lý dữ liệu khách hàng",
  description: "Tổng hợp dữ liệu khách hàng từ nhiều nguồn, đối chiếu hồ sơ và chọn thông tin đại diện.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi" className={`${beVietnamPro.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <body className={beVietnamPro.className}>
        <Script id="masterone-theme" strategy="beforeInteractive" dangerouslySetInnerHTML={{ __html: `var saved=null;try{saved=localStorage.getItem('masterone.theme')}catch(e){};var initial=saved==='light'||saved==='dark'?saved:'light';if(saved!=='light'&&saved!=='dark'){try{initial=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}catch(e){}}document.documentElement.dataset.theme=initial;` }} />
        {children}
      </body>
    </html>
  );
}
