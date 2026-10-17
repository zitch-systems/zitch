import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Movies = () => (
  <FeatureScreen
    title="Movies & Events"
    icon="movie"
    tagline="Cinema and event ticket purchases are not available in Zitch yet."
    points={[
      { icon: 'movie', title: 'Cinemas', sub: 'Supported venues will be listed when available' },
      { icon: 'ticket', title: 'Events', sub: 'Listings and ticket prices will be confirmed' },
      { icon: 'help', title: 'Questions', sub: 'Contact support for service availability' },
    ]}
    primaryLabel="Contact support"
    note="No ticket is purchased and no launch alert is registered from this page."
  />
);

export default Movies;
