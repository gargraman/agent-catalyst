import type { Metadata } from "next";
// Font swap for a new org happens HERE: next/font needs static, module-scope calls,
// so a CSS variable alone can't change the typeface. Keep the `variable` names in sync
// with --font-sans/--font-mono in src/app/brand.css. See frontend/BRANDING.md.
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ORG } from "@/lib/org";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: `${ORG.productName} - ${ORG.productTagline}`,
  description: ORG.productDescription,
};

// No-flash theme bootstrap: default dark, opt into light from localStorage.
const themeScript = `(function(){try{var t=localStorage.getItem('ac-theme');if(t==='light'){document.documentElement.classList.add('light');}}catch(e){}})();`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
