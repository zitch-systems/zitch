import React from 'react';
import Svg, { Path, Rect, Circle } from 'react-native-svg';

/**
 * ZIcon — direct port of the prototype's inline SVG icon set
 * (docs/design_handoff_zitch_revamp/shared.jsx) to react-native-svg.
 *
 * Each entry is a render function returning the icon's child elements so we can
 * support the few icons that use filled circles (dice) alongside stroked paths.
 */
type IconProps = { size?: number; color?: string; stroke?: number };

const P: Record<string, (color: string) => React.ReactNode> = {
  bell: () => [
    <Path key="a" d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />,
    <Path key="b" d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />,
  ],
  eye: () => [
    <Path key="a" d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />,
    <Circle key="b" cx={12} cy={12} r={3} />,
  ],
  eyeoff: () => [
    <Path key="a" d="M10.7 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a13 13 0 0 1-1.7 2.4M6.6 6.6A13 13 0 0 0 2 12s3.5 7 10 7a10 10 0 0 0 3.4-.6" />,
    <Path key="b" d="m9.9 9.9a3 3 0 0 0 4.2 4.2" />,
    <Path key="c" d="m2 2 20 20" />,
  ],
  scan: () => [
    <Path key="a" d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />,
    <Path key="b" d="M3 12h18" />,
  ],
  deposit: () => [
    <Path key="a" d="M12 3v12" />,
    <Path key="b" d="m7 10 5 5 5-5" />,
    <Path key="c" d="M5 21h14" />,
  ],
  withdraw: () => [
    <Path key="a" d="M12 21V9" />,
    <Path key="b" d="m7 14 5-5 5 5" />,
    <Path key="c" d="M5 3h14" />,
  ],
  send: () => [
    <Path key="a" d="M22 2 11 13" />,
    <Path key="b" d="M22 2 15 22l-4-9-9-4 20-7Z" />,
  ],
  airtime: () => [
    <Rect key="a" x={5} y={2} width={14} height={20} rx={2.5} />,
    <Path key="b" d="M11 18h2" />,
  ],
  data: () => [
    <Path key="a" d="M5 12.5a11 11 0 0 1 14 0M8.5 16a6 6 0 0 1 7 0M2 9a15 15 0 0 1 20 0" />,
    <Path key="b" d="M12 20h.01" />,
  ],
  bills: () => [<Path key="a" d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z" />],
  loan: () => [
    <Circle key="a" cx={9} cy={9} r={6.2} />,
    <Path key="b" d="M18.1 10.4A6 6 0 1 1 10.4 18" />,
    <Path key="c" d="M8.3 6.6h1.6a1.4 1.4 0 0 1 0 2.8H8.3h1.7a1.4 1.4 0 0 1 0 2.8H8.3M9.4 5.6v8" />,
  ],
  movie: () => [
    <Path key="a" d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />,
    <Path key="b" d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z" />,
    <Path key="c" d="m6.2 5.3 3.1 3.9M12.4 3.4l3.1 4" />,
  ],
  insurance: () => [
    <Path key="a" d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />,
    <Path key="b" d="m9 12 2 2 4-4" />,
  ],
  remita: () => [
    <Path key="a" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />,
    <Path key="b" d="M14 2v6h6" />,
    <Path key="c" d="M16 13H8M16 17H8M10 9H8" />,
  ],
  jamb: () => [
    <Path key="a" d="M22 10 12 5 2 10l10 5 10-5Z" />,
    <Path key="b" d="M6 12v5c3 2.7 9 2.7 12 0v-5" />,
  ],
  save: () => [
    <Path key="a" d="M2 11 12 4l10 7" />,
    <Path key="b" d="M4 10v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />,
    <Path key="c" d="M9 20v-5h6v5" />,
  ],
  convert: () => [
    <Path key="a" d="m17 2 4 4-4 4" />,
    <Path key="b" d="M3 11V10a4 4 0 0 1 4-4h14" />,
    <Path key="c" d="m7 22-4-4 4-4" />,
    <Path key="d" d="M21 13v1a4 4 0 0 1-4 4H3" />,
  ],
  more: () => [
    <Rect key="a" x={3} y={3} width={7} height={7} rx={2} />,
    <Rect key="b" x={14} y={3} width={7} height={7} rx={2} />,
    <Rect key="c" x={14} y={14} width={7} height={7} rx={2} />,
    <Rect key="d" x={3} y={14} width={7} height={7} rx={2} />,
  ],
  search: () => [
    <Circle key="a" cx={11} cy={11} r={7} />,
    <Path key="b" d="m21 21-4.3-4.3" />,
  ],
  home: () => [
    <Path key="a" d="m3 10 9-7 9 7v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />,
    <Path key="b" d="M9 21v-7h6v7" />,
  ],
  wallet: () => [
    <Path key="a" d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2" />,
    <Path key="b" d="M21 12a2 2 0 0 0-2-2h-4a2 2 0 0 0 0 8h4a2 2 0 0 0 2-2Z" />,
    <Path key="c" d="M17 14h.01" />,
  ],
  chart: () => [
    <Path key="a" d="M3 3v18h18" />,
    <Path key="b" d="M7 16v-5M12 16V8M17 16v-3" />,
  ],
  user: () => [
    <Circle key="a" cx={12} cy={8} r={4} />,
    <Path key="b" d="M4 20a8 8 0 0 1 16 0" />,
  ],
  plus: () => [<Path key="a" d="M12 5v14M5 12h14" />],
  right: () => [<Path key="a" d="m9 18 6-6-6-6" />],
  down: () => [<Path key="a" d="m6 9 6 6 6-6" />],
  up: () => [<Path key="a" d="m18 15-6-6-6 6" />],
  left: () => [
    <Path key="a" d="m12 19-7-7 7-7" />,
    <Path key="b" d="M19 12H5" />,
  ],
  ticket: () => [
    <Path key="a" d="M3 9a3 3 0 0 0 0 6v2a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2Z" />,
    <Path key="b" d="M13 5v2M13 11v2M13 17v2" />,
  ],
  check: () => [<Path key="a" d="M20 6 9 17l-5-5" />],
  spark: () => [<Path key="a" d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9Z" />],
  phone: () => [
    <Path key="a" d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92Z" />,
  ],
  mail: () => [
    <Rect key="a" x={2} y={4} width={20} height={16} rx={2} />,
    <Path key="b" d="m22 7-10 5L2 7" />,
  ],
  chat: () => [<Path key="a" d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />],
  gift: () => [
    <Rect key="a" x={3} y={8} width={18} height={4} rx={1} />,
    <Path key="b" d="M12 8v13M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" />,
    <Path key="c" d="M12 8S10.5 3 7.5 3 5 6 5 6s2 2 7 2ZM12 8s1.5-5 4.5-5S19 6 19 6s-2 2-7 2Z" />,
  ],
  copy: () => [
    <Rect key="a" x={9} y={9} width={11} height={11} rx={2} />,
    <Path key="b" d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2" />,
  ],
  share: () => [
    <Circle key="a" cx={18} cy={5} r={3} />,
    <Circle key="b" cx={6} cy={12} r={3} />,
    <Circle key="c" cx={18} cy={19} r={3} />,
    <Path key="d" d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" />,
  ],
  download: () => [
    <Path key="a" d="M12 3v12" />,
    <Path key="b" d="m7 11 5 4 5-4" />,
    <Path key="c" d="M5 21h14" />,
  ],
  history: () => [
    <Path key="a" d="M3 12a9 9 0 1 0 3-6.7L3 8" />,
    <Path key="b" d="M3 4v4h4" />,
    <Path key="c" d="M12 8v4l3 2" />,
  ],
  settings: () => [
    <Circle key="a" cx={12} cy={12} r={3} />,
    <Path key="b" d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 6.8 19l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 13.3H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 6.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 4.6V4a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" />,
  ],
  qr: () => [
    <Rect key="a" x={3} y={3} width={7} height={7} rx={1} />,
    <Rect key="b" x={14} y={3} width={7} height={7} rx={1} />,
    <Rect key="c" x={3} y={14} width={7} height={7} rx={1} />,
    <Path key="d" d="M14 14h3v3M21 14v.01M14 21h.01M21 17v4h-4" />,
  ],
  lock: () => [
    <Rect key="a" x={4} y={11} width={16} height={10} rx={2} />,
    <Path key="b" d="M8 11V7a4 4 0 0 1 8 0v4" />,
  ],
  help: () => [
    <Path key="a" d="M3 14v-2a9 9 0 0 1 18 0v2" />,
    <Path key="b" d="M21 15.5a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2Z" />,
    <Path key="c" d="M3 15.5a2 2 0 0 0 2 2h1v-6H5a2 2 0 0 0-2 2Z" />,
  ],
  tv: () => [
    <Rect key="a" x={3} y={7} width={18} height={13} rx={2.5} />,
    <Path key="b" d="m8 3 4 4 4-4" />,
  ],
  dice: (color: string) => [
    <Rect key="a" x={4} y={4} width={16} height={16} rx={3.5} />,
    <Circle key="b" cx={9} cy={9} r={1.25} fill={color} stroke="none" />,
    <Circle key="c" cx={15} cy={15} r={1.25} fill={color} stroke="none" />,
    <Circle key="d" cx={15} cy={9} r={1.25} fill={color} stroke="none" />,
    <Circle key="e" cx={9} cy={15} r={1.25} fill={color} stroke="none" />,
  ],
  bank: () => [
    <Path key="a" d="M3 10 12 4l9 6" />,
    <Path key="b" d="M5 10v9M19 10v9M9.5 10v9M14.5 10v9" />,
    <Path key="c" d="M3 21h18" />,
  ],
  card: () => [
    <Rect key="a" x={2} y={5} width={20} height={14} rx={2.5} />,
    <Path key="b" d="M2 10h20" />,
    <Path key="c" d="M6 15h4" />,
  ],
  fixed: () => [
    <Rect key="a" x={3} y={8} width={18} height={13} rx={2.5} />,
    <Path key="b" d="M8 8V6a4 4 0 0 1 8 0v2" />,
    <Path key="c" d="M12 13v3" />,
  ],
  invite: () => [
    <Path key="a" d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />,
    <Circle key="b" cx={9} cy={7} r={4} />,
    <Path key="c" d="M19 8v6M22 11h-6" />,
  ],
  fingerprint: () => [
    <Path key="a" d="M12 10a2 2 0 0 0-2 2c0 1.6-.4 3.2-1.1 4.6" />,
    <Path key="b" d="M12 6.5A5.5 5.5 0 0 1 17.5 12c0 2-.3 3.6-.9 5.2" />,
    <Path key="c" d="M9.2 18.8c.5-1 1-2.6 1-4.8a1.8 1.8 0 0 1 3.6 0c0 1.1-.1 2.1-.4 3.1" />,
    <Path key="d" d="M6 14.5c.4-1 .6-2 .6-3A5.4 5.4 0 0 1 12 6c1.1 0 2.1.3 3 .8" />,
    <Path key="e" d="M3.6 11.5A8.5 8.5 0 0 1 8 4.8" />,
  ],
  faceid: () => [
    <Path key="a" d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />,
    <Path key="b" d="M9 10v1M15 10v1M12 9.5v3l-1 .8" />,
    <Path key="c" d="M9.3 15a3.6 3.6 0 0 0 5.4 0" />,
  ],
  x: () => [<Path key="a" d="M18 6 6 18M6 6l12 12" />],
};

export type IconName = keyof typeof P;

const ZIcon = ({ name, size = 22, color = '#000', stroke = 1.75 }: IconProps & { name: string }) => {
  const render = P[name];
  if (!render) return null;
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {render(color)}
    </Svg>
  );
};

export default ZIcon;
