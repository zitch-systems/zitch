// data.js — Zitch prototype data (services, networks, billers, beneficiaries, txns)
window.ZDATA = (function () {
  const NETWORKS = [
    { id: 'mtn', name: 'MTN', color: '#FFCC00', fg: '#0A0A0A', logo: '/static/console/assets/logos/mtn.jpg' },
    { id: 'airtel', name: 'Airtel', color: '#E40000', fg: '#fff', logo: '/static/console/assets/logos/airtel.svg' },
    { id: 'glo', name: 'Glo', color: '#2BB24C', fg: '#fff', logo: '/static/console/assets/logos/glo.jpg' },
    { id: '9mobile', name: '9mobile', color: '#0A8A3D', fg: '#fff', logo: '/static/console/assets/logos/9mobile.svg' },
  ];

  const DATA_PLANS = {
    mtn: [
      { id: 'm1', label: '1.5GB', sub: '30 days', price: 1000 },
      { id: 'm2', label: '3GB', sub: '30 days', price: 1500 },
      { id: 'm3', label: '6GB', sub: '30 days', price: 2500 },
      { id: 'm4', label: '11GB', sub: '30 days', price: 4500 },
      { id: 'm5', label: '40GB', sub: '30 days', price: 11000 },
      { id: 'm6', label: '75GB', sub: '60 days', price: 18000 },
    ],
    airtel: [
      { id: 'a1', label: '1.5GB', sub: '30 days', price: 1000 },
      { id: 'a2', label: '4.5GB', sub: '30 days', price: 2000 },
      { id: 'a3', label: '10GB', sub: '30 days', price: 4000 },
      { id: 'a4', label: '18GB', sub: '30 days', price: 6000 },
    ],
    glo: [
      { id: 'g1', label: '2GB', sub: '30 days', price: 1000 },
      { id: 'g2', label: '5.8GB', sub: '30 days', price: 2000 },
      { id: 'g3', label: '12GB', sub: '30 days', price: 4000 },
    ],
    '9mobile': [
      { id: 'n1', label: '1GB', sub: '30 days', price: 1000 },
      { id: 'n2', label: '4.5GB', sub: '30 days', price: 2500 },
      { id: 'n3', label: '11GB', sub: '30 days', price: 5000 },
    ],
  };

  const CABLE = [
    { id: 'dstv', name: 'DSTV', color: '#0A66C2', logo: '/static/console/assets/logos/dstv.svg' },
    { id: 'gotv', name: 'GOtv', color: '#92C020', logo: '/static/console/assets/logos/gotv.svg' },
    { id: 'startimes', name: 'StarTimes', color: '#F47B20', logo: '/static/console/assets/logos/startimes.png' },
    { id: 'showmax', name: 'Showmax', color: '#1A1A2E' },
  ];
  const CABLE_PLANS = {
    dstv: [
      { id: 'd1', label: 'Padi', sub: 'DStv Padi', price: 4400 },
      { id: 'd2', label: 'Yanga', sub: 'DStv Yanga', price: 6000 },
      { id: 'd3', label: 'Confam', sub: 'DStv Confam', price: 11000 },
      { id: 'd4', label: 'Compact', sub: 'DStv Compact', price: 19000 },
      { id: 'd5', label: 'Compact Plus', sub: 'DStv Compact Plus', price: 30000 },
      { id: 'd6', label: 'Premium', sub: 'DStv Premium', price: 44500 },
    ],
    gotv: [
      { id: 'gt1', label: 'Smallie', sub: 'GOtv Smallie', price: 1900 },
      { id: 'gt2', label: 'Jinja', sub: 'GOtv Jinja', price: 3900 },
      { id: 'gt3', label: 'Jolli', sub: 'GOtv Jolli', price: 5800 },
      { id: 'gt4', label: 'Max', sub: 'GOtv Max', price: 8500 },
    ],
    startimes: [
      { id: 's1', label: 'Nova', sub: 'Nova bouquet', price: 1900 },
      { id: 's2', label: 'Basic', sub: 'Basic bouquet', price: 4000 },
      { id: 's3', label: 'Smart', sub: 'Smart bouquet', price: 5100 },
    ],
    showmax: [
      { id: 'sm1', label: 'Mobile', sub: 'Showmax Mobile', price: 1600 },
      { id: 'sm2', label: 'Standard', sub: 'Showmax Standard', price: 3500 },
      { id: 'sm3', label: 'Pro', sub: 'Showmax Pro', price: 6300 },
    ],
  };

  const DISCOS = [
    { id: 'ikeja', name: 'Ikeja Electric', color: '#E08A00' },
    { id: 'eko', name: 'Eko Electric', color: '#1E5BB8' },
    { id: 'aedc', name: 'Abuja (AEDC)', color: '#0B7A3B' },
    { id: 'ibedc', name: 'Ibadan (IBEDC)', color: '#7A1FA2' },
    { id: 'phed', name: 'Port Harcourt', color: '#C0392B' },
    { id: 'kaduna', name: 'Kaduna Electric', color: '#16667E' },
  ];

  const BETTING = [
    { id: 'bet9ja', name: 'Bet9ja', color: '#0B7A3B' },
    { id: 'sporty', name: 'SportyBet', color: '#E1241B' },
    { id: 'onexbet', name: '1xBet', color: '#1A6BB5' },
    { id: 'betking', name: 'BetKing', color: '#1B1B1B' },
    { id: 'nairabet', name: 'NairaBet', color: '#1E8B45' },
    { id: 'msport', name: 'MSport', color: '#E8530E' },
  ];

  const EXAMS = [
    { id: 'waec', name: 'WAEC', sub: 'Result Checker PIN', color: '#0B7A3B', price: 3500 },
    { id: 'neco', name: 'NECO', sub: 'Result Token', color: '#1E5BB8', price: 1300 },
    { id: 'jamb', name: 'JAMB', sub: 'UTME / DE PIN', color: '#7A1FA2', price: 6200 },
    { id: 'nabteb', name: 'NABTEB', sub: 'Result Checker', color: '#C0392B', price: 1000 },
  ];

  const BANKS = [
    { id: 'gtb', name: 'GTBank', color: '#E35205' },
    { id: 'access', name: 'Access Bank', color: '#00488D' },
    { id: 'zenith', name: 'Zenith Bank', color: '#E2231A' },
    { id: 'uba', name: 'UBA', color: '#D4122A' },
    { id: 'kuda', name: 'Kuda', color: '#40196D' },
    { id: 'opay', name: 'OPay', color: '#1A8E5F' },
    { id: 'palmpay', name: 'PalmPay', color: '#6C2FB3' },
    { id: 'firstbank', name: 'First Bank', color: '#0B4DA2' },
  ];

  const BENEFICIARIES = [
    { id: 'b1', name: 'Chioma Okeke', acct: '0123456789', bank: 'GTBank', init: 'CO', color: '#E35205' },
    { id: 'b2', name: 'Tunde Bakare', acct: '2233445566', bank: 'Access Bank', init: 'TB', color: '#00488D' },
    { id: 'b3', name: 'Amaka Zitch', acct: '9012345678', bank: 'Zitch', init: 'AZ', color: '#0FA295' },
    { id: 'b4', name: 'David Â.', acct: '0567891234', bank: 'Kuda', init: 'DA', color: '#40196D' },
  ];

  const QUICK_AMTS = [200, 500, 1000, 2000, 5000, 10000];

  const TXNS = [
    { id: 't1', mono: 'AT', t: 'Airtime — MTN', cat: 'airtime', amt: -600, time: 'Today, 2:14pm', col: '#FFCC00', status: 'Successful' },
    { id: 't2', mono: 'DS', t: 'DSTV Compact Plus', cat: 'tv', amt: -30000, time: 'Today, 9:02am', col: '#0A66C2', status: 'Successful' },
    { id: 't3', mono: 'ZW', t: 'Wallet top-up', cat: 'fund', amt: 150000, time: 'Yesterday, 6:31pm', col: '#0FA295', status: 'Successful' },
    { id: 't4', mono: 'CO', t: 'Transfer — Chioma Okeke', cat: 'transfer', amt: -25000, time: 'Yesterday, 1:10pm', col: '#E35205', status: 'Successful' },
    { id: 't5', mono: 'IK', t: 'Ikeja Electric', cat: 'electricity', amt: -5000, time: 'May 28, 8:45am', col: '#E08A00', status: 'Successful' },
    { id: 't6', mono: 'B9', t: 'Bet9ja funding', cat: 'betting', amt: -3000, time: 'May 27, 7:20pm', col: '#0B7A3B', status: 'Pending' },
    { id: 't7', mono: 'DA', t: 'Data — Airtel 10GB', cat: 'data', amt: -4000, time: 'May 26, 11:02am', col: '#E40000', status: 'Successful' },
  ];

  return { NETWORKS, DATA_PLANS, CABLE, CABLE_PLANS, DISCOS, BETTING, EXAMS, BANKS, BENEFICIARIES, QUICK_AMTS, TXNS };
})();
