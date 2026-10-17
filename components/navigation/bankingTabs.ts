export const BANKING_TABS = [
  { name: 'home', icon: 'home', label: 'Home' },
  { name: 'wallet', icon: 'wallet', label: 'Wallet' },
  { name: 'cards', icon: 'card', label: 'Cards' },
  { name: 'me', icon: 'user', label: 'Me' },
] as const;

const PARENT_TAB: Record<string, string> = {
  history: 'wallet', analysis: 'wallet', txndetail: 'wallet',
  notifications: 'home', convert: 'home', loan: 'home',
};

export const selectedBankingTab = (route?: string) => route ? PARENT_TAB[route] || route : 'home';
