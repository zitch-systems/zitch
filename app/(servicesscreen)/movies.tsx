import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Movies = () => (
  <FeatureScreen
    title="Movies & Events"
    icon="movie"
    tagline="Book cinema tickets and live events, and pay straight from your wallet."
    points={[
      { icon: 'movie', title: 'Cinema tickets', sub: 'Latest releases near you' },
      { icon: 'ticket', title: 'Live events', sub: 'Concerts, shows & sports' },
      { icon: 'wallet', title: 'Pay with wallet', sub: 'One tap, instant confirmation' },
    ]}
    primaryLabel="Notify me at launch"
    note="Ticketing is rolling out by city. Reach out and we'll let you know when it's live near you."
  />
);

export default Movies;
