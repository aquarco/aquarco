import { Query } from './queries.js'
import { Mutation } from './mutations.js'
import { Task, Repository, DateTime, JSON_Scalar } from './types.js'

export const resolvers = {
  Query,
  Mutation,
  Task,
  Repository,
  DateTime,
  JSON: JSON_Scalar,
}
