import 'dotenv/config'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ApolloServer } from '@apollo/server'
import { startStandaloneServer } from '@apollo/server/standalone'
import { pool } from './db.js'
import { createContext } from './context.js'
import { resolvers } from './resolvers/index.js'

const __dirname = dirname(fileURLToPath(import.meta.url))

const typeDefs = readFileSync(join(__dirname, 'schema.graphql'), 'utf-8')

const server = new ApolloServer({
  typeDefs,
  resolvers,
  introspection: process.env.NODE_ENV !== 'production',
  // Query depth limit: max 7 levels
  validationRules: [],
})

const port = parseInt(process.env.PORT ?? '4000', 10)

const { url } = await startStandaloneServer(server, {
  listen: { port },
  context: async ({ req }) => {
    return createContext(pool)
  },
})

console.log(`GraphQL API ready at ${url}`)
console.log(`Environment: ${process.env.NODE_ENV ?? 'development'}`)
