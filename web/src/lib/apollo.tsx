'use client'

import { ApolloClient, InMemoryCache, HttpLink, ApolloProvider } from '@apollo/client'
import { type ReactNode, useMemo } from 'react'

function makeClient() {
  const uri =
    typeof window !== 'undefined'
      ? (process.env.NEXT_PUBLIC_API_URL ?? '/api/graphql')
      : 'http://api:4000/graphql'

  return new ApolloClient({
    link: new HttpLink({ uri }),
    cache: new InMemoryCache(),
    defaultOptions: {
      watchQuery: { fetchPolicy: 'cache-and-network' },
    },
  })
}

interface ApolloWrapperProps {
  children: ReactNode
}

export function ApolloWrapper({ children }: ApolloWrapperProps) {
  const client = useMemo(() => makeClient(), [])
  return <ApolloProvider client={client}>{children}</ApolloProvider>
}

export default ApolloWrapper
