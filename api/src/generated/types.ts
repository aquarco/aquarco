import { GraphQLResolveInfo, GraphQLScalarType, GraphQLScalarTypeConfig } from 'graphql';
import { Context } from '../context';
export type Maybe<T> = T | null;
export type InputMaybe<T> = Maybe<T>;
export type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
export type MakeOptional<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]?: Maybe<T[SubKey]> };
export type MakeMaybe<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]: Maybe<T[SubKey]> };
export type MakeEmpty<T extends { [key: string]: unknown }, K extends keyof T> = { [_ in K]?: never };
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
export type RequireFields<T, K extends keyof T> = Omit<T, K> & { [P in K]-?: NonNullable<T[P]> };
/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string; }
  String: { input: string; output: string; }
  Boolean: { input: boolean; output: boolean; }
  Int: { input: number; output: number; }
  Float: { input: number; output: number; }
  DateTime: { input: string; output: string; }
  JSON: { input: unknown; output: unknown; }
};

export type AgentInstance = {
  __typename?: 'AgentInstance';
  activeCount: Scalars['Int']['output'];
  agentName: Scalars['String']['output'];
  lastExecutionAt?: Maybe<Scalars['DateTime']['output']>;
  totalExecutions: Scalars['Int']['output'];
  totalTokensUsed: Scalars['Int']['output'];
};

export type ClaudeAuthStatus = {
  __typename?: 'ClaudeAuthStatus';
  authenticated: Scalars['Boolean']['output'];
  email?: Maybe<Scalars['String']['output']>;
};

export type ClaudeLoginResult = {
  __typename?: 'ClaudeLoginResult';
  email?: Maybe<Scalars['String']['output']>;
  error?: Maybe<Scalars['String']['output']>;
  success: Scalars['Boolean']['output'];
};

export type ClaudeLoginStart = {
  __typename?: 'ClaudeLoginStart';
  authorizeUrl: Scalars['String']['output'];
  expiresIn: Scalars['Int']['output'];
};

export enum CloneStatus {
  Cloning = 'cloning',
  Error = 'error',
  Pending = 'pending',
  Ready = 'ready'
}

export type ContextEntry = {
  __typename?: 'ContextEntry';
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['ID']['output'];
  key: Scalars['String']['output'];
  stageNumber?: Maybe<Scalars['Int']['output']>;
  taskId: Scalars['ID']['output'];
  valueFileRef?: Maybe<Scalars['String']['output']>;
  valueJson?: Maybe<Scalars['JSON']['output']>;
  valueText?: Maybe<Scalars['String']['output']>;
  valueType: Scalars['String']['output'];
};

export type CreateTaskInput = {
  initialContext?: InputMaybe<Scalars['JSON']['input']>;
  pipeline?: InputMaybe<Scalars['String']['input']>;
  priority?: InputMaybe<Scalars['Int']['input']>;
  repository: Scalars['String']['input'];
  source: Scalars['String']['input'];
  sourceRef?: InputMaybe<Scalars['String']['input']>;
  title: Scalars['String']['input'];
};

export type DashboardStats = {
  __typename?: 'DashboardStats';
  activeAgents: Scalars['Int']['output'];
  blockedTasks: Scalars['Int']['output'];
  completedTasks: Scalars['Int']['output'];
  executingTasks: Scalars['Int']['output'];
  failedTasks: Scalars['Int']['output'];
  pendingTasks: Scalars['Int']['output'];
  tasksByPipeline: Array<PipelineCount>;
  tasksByRepository: Array<RepositoryCount>;
  totalTasks: Scalars['Int']['output'];
  totalTokensToday: Scalars['Int']['output'];
};

export type Error = {
  __typename?: 'Error';
  field?: Maybe<Scalars['String']['output']>;
  message: Scalars['String']['output'];
};

export type GithubAuthStatus = {
  __typename?: 'GithubAuthStatus';
  authenticated: Scalars['Boolean']['output'];
  username?: Maybe<Scalars['String']['output']>;
};

export type GithubDeviceCode = {
  __typename?: 'GithubDeviceCode';
  expiresIn: Scalars['Int']['output'];
  userCode: Scalars['String']['output'];
  verificationUri: Scalars['String']['output'];
};

export type GithubLoginResult = {
  __typename?: 'GithubLoginResult';
  error?: Maybe<Scalars['String']['output']>;
  success: Scalars['Boolean']['output'];
  username?: Maybe<Scalars['String']['output']>;
};

export type GithubRepo = {
  __typename?: 'GithubRepo';
  defaultBranch: Scalars['String']['output'];
  description?: Maybe<Scalars['String']['output']>;
  isPrivate: Scalars['Boolean']['output'];
  nameWithOwner: Scalars['String']['output'];
  url: Scalars['String']['output'];
};

export type Mutation = {
  __typename?: 'Mutation';
  cancelTask: TaskPayload;
  claudeLoginPoll: ClaudeLoginResult;
  claudeLoginStart: ClaudeLoginStart;
  claudeLogout: Scalars['Boolean']['output'];
  claudeSubmitCode: ClaudeLoginResult;
  createTask: TaskPayload;
  githubLoginPoll: GithubLoginResult;
  githubLoginStart: GithubDeviceCode;
  githubLogout: Scalars['Boolean']['output'];
  registerRepository: RepositoryPayload;
  removeRepository: RepositoryPayload;
  retryClone: RepositoryPayload;
  retryTask: TaskPayload;
  unblockTask: TaskPayload;
  updateTaskStatus: TaskPayload;
};


export type MutationCancelTaskArgs = {
  id: Scalars['ID']['input'];
};


export type MutationClaudeSubmitCodeArgs = {
  code: Scalars['String']['input'];
};


export type MutationCreateTaskArgs = {
  input: CreateTaskInput;
};


export type MutationRegisterRepositoryArgs = {
  input: RegisterRepositoryInput;
};


export type MutationRemoveRepositoryArgs = {
  name: Scalars['String']['input'];
};


export type MutationRetryCloneArgs = {
  name: Scalars['String']['input'];
};


export type MutationRetryTaskArgs = {
  id: Scalars['ID']['input'];
};


export type MutationUnblockTaskArgs = {
  id: Scalars['ID']['input'];
  resolution: Scalars['String']['input'];
};


export type MutationUpdateTaskStatusArgs = {
  id: Scalars['ID']['input'];
  status: TaskStatus;
};

export type PipelineCount = {
  __typename?: 'PipelineCount';
  count: Scalars['Int']['output'];
  pipeline: Scalars['String']['output'];
};

export type PipelineStatus = {
  __typename?: 'PipelineStatus';
  currentStage: Scalars['Int']['output'];
  pipeline?: Maybe<Scalars['String']['output']>;
  stages: Array<Stage>;
  status: TaskStatus;
  taskId: Scalars['ID']['output'];
  totalStages: Scalars['Int']['output'];
};

export type Query = {
  __typename?: 'Query';
  agentInstances: Array<AgentInstance>;
  claudeAuthStatus: ClaudeAuthStatus;
  dashboardStats: DashboardStats;
  githubAuthStatus: GithubAuthStatus;
  githubRepositories: Array<GithubRepo>;
  pipelineStatus?: Maybe<PipelineStatus>;
  repositories: Array<Repository>;
  repository?: Maybe<Repository>;
  task?: Maybe<Task>;
  tasks: TaskConnection;
};


export type QueryPipelineStatusArgs = {
  taskId: Scalars['ID']['input'];
};


export type QueryRepositoryArgs = {
  name: Scalars['String']['input'];
};


export type QueryTaskArgs = {
  id: Scalars['ID']['input'];
};


export type QueryTasksArgs = {
  limit?: InputMaybe<Scalars['Int']['input']>;
  offset?: InputMaybe<Scalars['Int']['input']>;
  repository?: InputMaybe<Scalars['String']['input']>;
  status?: InputMaybe<TaskStatus>;
};

export type RegisterRepositoryInput = {
  branch?: InputMaybe<Scalars['String']['input']>;
  cloneDir?: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
  pollers?: InputMaybe<Array<Scalars['String']['input']>>;
  url: Scalars['String']['input'];
};

export type Repository = {
  __typename?: 'Repository';
  branch: Scalars['String']['output'];
  cloneDir: Scalars['String']['output'];
  cloneStatus: CloneStatus;
  deployPublicKey?: Maybe<Scalars['String']['output']>;
  errorMessage?: Maybe<Scalars['String']['output']>;
  headSha?: Maybe<Scalars['String']['output']>;
  lastClonedAt?: Maybe<Scalars['DateTime']['output']>;
  lastPulledAt?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  pollers: Array<Scalars['String']['output']>;
  taskCount: Scalars['Int']['output'];
  url: Scalars['String']['output'];
};

export type RepositoryCount = {
  __typename?: 'RepositoryCount';
  count: Scalars['Int']['output'];
  repository: Scalars['String']['output'];
};

export type RepositoryPayload = {
  __typename?: 'RepositoryPayload';
  errors?: Maybe<Array<Error>>;
  repository?: Maybe<Repository>;
};

export type Stage = {
  __typename?: 'Stage';
  agent?: Maybe<Scalars['String']['output']>;
  agentVersion?: Maybe<Scalars['String']['output']>;
  category: Scalars['String']['output'];
  completedAt?: Maybe<Scalars['DateTime']['output']>;
  errorMessage?: Maybe<Scalars['String']['output']>;
  id: Scalars['ID']['output'];
  rawOutput?: Maybe<Scalars['String']['output']>;
  retryCount: Scalars['Int']['output'];
  stageNumber: Scalars['Int']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: StageStatus;
  structuredOutput?: Maybe<Scalars['JSON']['output']>;
  taskId: Scalars['ID']['output'];
  tokensInput?: Maybe<Scalars['Int']['output']>;
  tokensOutput?: Maybe<Scalars['Int']['output']>;
};

export enum StageStatus {
  Completed = 'completed',
  Executing = 'executing',
  Failed = 'failed',
  Pending = 'pending',
  Skipped = 'skipped'
}

export type Subscription = {
  __typename?: 'Subscription';
  pipelineProgress: PipelineStatus;
  taskUpdated: Task;
};


export type SubscriptionPipelineProgressArgs = {
  taskId: Scalars['ID']['input'];
};


export type SubscriptionTaskUpdatedArgs = {
  repository?: InputMaybe<Scalars['String']['input']>;
};

export type Task = {
  __typename?: 'Task';
  assignedAgent?: Maybe<Scalars['String']['output']>;
  completedAt?: Maybe<Scalars['DateTime']['output']>;
  context: Array<ContextEntry>;
  createdAt: Scalars['DateTime']['output'];
  currentStage: Scalars['Int']['output'];
  errorMessage?: Maybe<Scalars['String']['output']>;
  id: Scalars['ID']['output'];
  initialContext?: Maybe<Scalars['JSON']['output']>;
  pipeline: Scalars['String']['output'];
  priority: Scalars['Int']['output'];
  repository: Repository;
  retryCount: Scalars['Int']['output'];
  source: Scalars['String']['output'];
  sourceRef?: Maybe<Scalars['String']['output']>;
  stages: Array<Stage>;
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: TaskStatus;
  title: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type TaskConnection = {
  __typename?: 'TaskConnection';
  nodes: Array<Task>;
  totalCount: Scalars['Int']['output'];
};

export type TaskPayload = {
  __typename?: 'TaskPayload';
  errors?: Maybe<Array<Error>>;
  task?: Maybe<Task>;
};

export enum TaskStatus {
  Blocked = 'blocked',
  Completed = 'completed',
  Executing = 'executing',
  Failed = 'failed',
  Pending = 'pending',
  Queued = 'queued',
  Timeout = 'timeout'
}

export type WithIndex<TObject> = TObject & Record<string, any>;
export type ResolversObject<TObject> = WithIndex<TObject>;

export type ResolverTypeWrapper<T> = Promise<T> | T;


export type ResolverWithResolve<TResult, TParent, TContext, TArgs> = {
  resolve: ResolverFn<TResult, TParent, TContext, TArgs>;
};
export type Resolver<TResult, TParent = {}, TContext = {}, TArgs = {}> = ResolverFn<TResult, TParent, TContext, TArgs> | ResolverWithResolve<TResult, TParent, TContext, TArgs>;

export type ResolverFn<TResult, TParent, TContext, TArgs> = (
  parent: TParent,
  args: TArgs,
  context: TContext,
  info: GraphQLResolveInfo
) => Promise<TResult> | TResult;

export type SubscriptionSubscribeFn<TResult, TParent, TContext, TArgs> = (
  parent: TParent,
  args: TArgs,
  context: TContext,
  info: GraphQLResolveInfo
) => AsyncIterable<TResult> | Promise<AsyncIterable<TResult>>;

export type SubscriptionResolveFn<TResult, TParent, TContext, TArgs> = (
  parent: TParent,
  args: TArgs,
  context: TContext,
  info: GraphQLResolveInfo
) => TResult | Promise<TResult>;

export interface SubscriptionSubscriberObject<TResult, TKey extends string, TParent, TContext, TArgs> {
  subscribe: SubscriptionSubscribeFn<{ [key in TKey]: TResult }, TParent, TContext, TArgs>;
  resolve?: SubscriptionResolveFn<TResult, { [key in TKey]: TResult }, TContext, TArgs>;
}

export interface SubscriptionResolverObject<TResult, TParent, TContext, TArgs> {
  subscribe: SubscriptionSubscribeFn<any, TParent, TContext, TArgs>;
  resolve: SubscriptionResolveFn<TResult, any, TContext, TArgs>;
}

export type SubscriptionObject<TResult, TKey extends string, TParent, TContext, TArgs> =
  | SubscriptionSubscriberObject<TResult, TKey, TParent, TContext, TArgs>
  | SubscriptionResolverObject<TResult, TParent, TContext, TArgs>;

export type SubscriptionResolver<TResult, TKey extends string, TParent = {}, TContext = {}, TArgs = {}> =
  | ((...args: any[]) => SubscriptionObject<TResult, TKey, TParent, TContext, TArgs>)
  | SubscriptionObject<TResult, TKey, TParent, TContext, TArgs>;

export type TypeResolveFn<TTypes, TParent = {}, TContext = {}> = (
  parent: TParent,
  context: TContext,
  info: GraphQLResolveInfo
) => Maybe<TTypes> | Promise<Maybe<TTypes>>;

export type IsTypeOfResolverFn<T = {}, TContext = {}> = (obj: T, context: TContext, info: GraphQLResolveInfo) => boolean | Promise<boolean>;

export type NextResolverFn<T> = () => Promise<T>;

export type DirectiveResolverFn<TResult = {}, TParent = {}, TContext = {}, TArgs = {}> = (
  next: NextResolverFn<TResult>,
  parent: TParent,
  args: TArgs,
  context: TContext,
  info: GraphQLResolveInfo
) => TResult | Promise<TResult>;



/** Mapping between all available schema types and the resolvers types */
export type ResolversTypes = ResolversObject<{
  AgentInstance: ResolverTypeWrapper<AgentInstance>;
  Boolean: ResolverTypeWrapper<Scalars['Boolean']['output']>;
  ClaudeAuthStatus: ResolverTypeWrapper<ClaudeAuthStatus>;
  ClaudeLoginResult: ResolverTypeWrapper<ClaudeLoginResult>;
  ClaudeLoginStart: ResolverTypeWrapper<ClaudeLoginStart>;
  CloneStatus: null;
  ContextEntry: ResolverTypeWrapper<ContextEntry>;
  CreateTaskInput: CreateTaskInput;
  DashboardStats: ResolverTypeWrapper<DashboardStats>;
  DateTime: ResolverTypeWrapper<Scalars['DateTime']['output']>;
  Error: ResolverTypeWrapper<Error>;
  GithubAuthStatus: ResolverTypeWrapper<GithubAuthStatus>;
  GithubDeviceCode: ResolverTypeWrapper<GithubDeviceCode>;
  GithubLoginResult: ResolverTypeWrapper<GithubLoginResult>;
  GithubRepo: ResolverTypeWrapper<GithubRepo>;
  ID: ResolverTypeWrapper<Scalars['ID']['output']>;
  Int: ResolverTypeWrapper<Scalars['Int']['output']>;
  JSON: ResolverTypeWrapper<Scalars['JSON']['output']>;
  Mutation: ResolverTypeWrapper<{}>;
  PipelineCount: ResolverTypeWrapper<PipelineCount>;
  PipelineStatus: ResolverTypeWrapper<PipelineStatus>;
  Query: ResolverTypeWrapper<{}>;
  RegisterRepositoryInput: RegisterRepositoryInput;
  Repository: ResolverTypeWrapper<Repository>;
  RepositoryCount: ResolverTypeWrapper<RepositoryCount>;
  RepositoryPayload: ResolverTypeWrapper<RepositoryPayload>;
  Stage: ResolverTypeWrapper<Stage>;
  StageStatus: null;
  String: ResolverTypeWrapper<Scalars['String']['output']>;
  Subscription: ResolverTypeWrapper<{}>;
  Task: ResolverTypeWrapper<Task>;
  TaskConnection: ResolverTypeWrapper<TaskConnection>;
  TaskPayload: ResolverTypeWrapper<TaskPayload>;
  TaskStatus: null;
}>;

/** Mapping between all available schema types and the resolvers parents */
export type ResolversParentTypes = ResolversObject<{
  AgentInstance: AgentInstance;
  Boolean: Scalars['Boolean']['output'];
  ClaudeAuthStatus: ClaudeAuthStatus;
  ClaudeLoginResult: ClaudeLoginResult;
  ClaudeLoginStart: ClaudeLoginStart;
  ContextEntry: ContextEntry;
  CreateTaskInput: CreateTaskInput;
  DashboardStats: DashboardStats;
  DateTime: Scalars['DateTime']['output'];
  Error: Error;
  GithubAuthStatus: GithubAuthStatus;
  GithubDeviceCode: GithubDeviceCode;
  GithubLoginResult: GithubLoginResult;
  GithubRepo: GithubRepo;
  ID: Scalars['ID']['output'];
  Int: Scalars['Int']['output'];
  JSON: Scalars['JSON']['output'];
  Mutation: {};
  PipelineCount: PipelineCount;
  PipelineStatus: PipelineStatus;
  Query: {};
  RegisterRepositoryInput: RegisterRepositoryInput;
  Repository: Repository;
  RepositoryCount: RepositoryCount;
  RepositoryPayload: RepositoryPayload;
  Stage: Stage;
  String: Scalars['String']['output'];
  Subscription: {};
  Task: Task;
  TaskConnection: TaskConnection;
  TaskPayload: TaskPayload;
}>;

export type AgentInstanceResolvers<ContextType = Context, ParentType extends ResolversParentTypes['AgentInstance'] = ResolversParentTypes['AgentInstance']> = ResolversObject<{
  activeCount?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  agentName?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  lastExecutionAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  totalExecutions?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  totalTokensUsed?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type ClaudeAuthStatusResolvers<ContextType = Context, ParentType extends ResolversParentTypes['ClaudeAuthStatus'] = ResolversParentTypes['ClaudeAuthStatus']> = ResolversObject<{
  authenticated?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  email?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type ClaudeLoginResultResolvers<ContextType = Context, ParentType extends ResolversParentTypes['ClaudeLoginResult'] = ResolversParentTypes['ClaudeLoginResult']> = ResolversObject<{
  email?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  error?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  success?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type ClaudeLoginStartResolvers<ContextType = Context, ParentType extends ResolversParentTypes['ClaudeLoginStart'] = ResolversParentTypes['ClaudeLoginStart']> = ResolversObject<{
  authorizeUrl?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  expiresIn?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type CloneStatusResolvers = { CLONING: 'cloning', ERROR: 'error', PENDING: 'pending', READY: 'ready' };

export type ContextEntryResolvers<ContextType = Context, ParentType extends ResolversParentTypes['ContextEntry'] = ResolversParentTypes['ContextEntry']> = ResolversObject<{
  createdAt?: Resolver<ResolversTypes['DateTime'], ParentType, ContextType>;
  id?: Resolver<ResolversTypes['ID'], ParentType, ContextType>;
  key?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  stageNumber?: Resolver<Maybe<ResolversTypes['Int']>, ParentType, ContextType>;
  taskId?: Resolver<ResolversTypes['ID'], ParentType, ContextType>;
  valueFileRef?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  valueJson?: Resolver<Maybe<ResolversTypes['JSON']>, ParentType, ContextType>;
  valueText?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  valueType?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type DashboardStatsResolvers<ContextType = Context, ParentType extends ResolversParentTypes['DashboardStats'] = ResolversParentTypes['DashboardStats']> = ResolversObject<{
  activeAgents?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  blockedTasks?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  completedTasks?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  executingTasks?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  failedTasks?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  pendingTasks?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  tasksByPipeline?: Resolver<Array<ResolversTypes['PipelineCount']>, ParentType, ContextType>;
  tasksByRepository?: Resolver<Array<ResolversTypes['RepositoryCount']>, ParentType, ContextType>;
  totalTasks?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  totalTokensToday?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export interface DateTimeScalarConfig extends GraphQLScalarTypeConfig<ResolversTypes['DateTime'], any> {
  name: 'DateTime';
}

export type ErrorResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Error'] = ResolversParentTypes['Error']> = ResolversObject<{
  field?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  message?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type GithubAuthStatusResolvers<ContextType = Context, ParentType extends ResolversParentTypes['GithubAuthStatus'] = ResolversParentTypes['GithubAuthStatus']> = ResolversObject<{
  authenticated?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  username?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type GithubDeviceCodeResolvers<ContextType = Context, ParentType extends ResolversParentTypes['GithubDeviceCode'] = ResolversParentTypes['GithubDeviceCode']> = ResolversObject<{
  expiresIn?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  userCode?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  verificationUri?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type GithubLoginResultResolvers<ContextType = Context, ParentType extends ResolversParentTypes['GithubLoginResult'] = ResolversParentTypes['GithubLoginResult']> = ResolversObject<{
  error?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  success?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  username?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type GithubRepoResolvers<ContextType = Context, ParentType extends ResolversParentTypes['GithubRepo'] = ResolversParentTypes['GithubRepo']> = ResolversObject<{
  defaultBranch?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  description?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  isPrivate?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  nameWithOwner?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  url?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export interface JsonScalarConfig extends GraphQLScalarTypeConfig<ResolversTypes['JSON'], any> {
  name: 'JSON';
}

export type MutationResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Mutation'] = ResolversParentTypes['Mutation']> = ResolversObject<{
  cancelTask?: Resolver<ResolversTypes['TaskPayload'], ParentType, ContextType, RequireFields<MutationCancelTaskArgs, 'id'>>;
  claudeLoginPoll?: Resolver<ResolversTypes['ClaudeLoginResult'], ParentType, ContextType>;
  claudeLoginStart?: Resolver<ResolversTypes['ClaudeLoginStart'], ParentType, ContextType>;
  claudeLogout?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  claudeSubmitCode?: Resolver<ResolversTypes['ClaudeLoginResult'], ParentType, ContextType, RequireFields<MutationClaudeSubmitCodeArgs, 'code'>>;
  createTask?: Resolver<ResolversTypes['TaskPayload'], ParentType, ContextType, RequireFields<MutationCreateTaskArgs, 'input'>>;
  githubLoginPoll?: Resolver<ResolversTypes['GithubLoginResult'], ParentType, ContextType>;
  githubLoginStart?: Resolver<ResolversTypes['GithubDeviceCode'], ParentType, ContextType>;
  githubLogout?: Resolver<ResolversTypes['Boolean'], ParentType, ContextType>;
  registerRepository?: Resolver<ResolversTypes['RepositoryPayload'], ParentType, ContextType, RequireFields<MutationRegisterRepositoryArgs, 'input'>>;
  removeRepository?: Resolver<ResolversTypes['RepositoryPayload'], ParentType, ContextType, RequireFields<MutationRemoveRepositoryArgs, 'name'>>;
  retryClone?: Resolver<ResolversTypes['RepositoryPayload'], ParentType, ContextType, RequireFields<MutationRetryCloneArgs, 'name'>>;
  retryTask?: Resolver<ResolversTypes['TaskPayload'], ParentType, ContextType, RequireFields<MutationRetryTaskArgs, 'id'>>;
  unblockTask?: Resolver<ResolversTypes['TaskPayload'], ParentType, ContextType, RequireFields<MutationUnblockTaskArgs, 'id' | 'resolution'>>;
  updateTaskStatus?: Resolver<ResolversTypes['TaskPayload'], ParentType, ContextType, RequireFields<MutationUpdateTaskStatusArgs, 'id' | 'status'>>;
}>;

export type PipelineCountResolvers<ContextType = Context, ParentType extends ResolversParentTypes['PipelineCount'] = ResolversParentTypes['PipelineCount']> = ResolversObject<{
  count?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  pipeline?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type PipelineStatusResolvers<ContextType = Context, ParentType extends ResolversParentTypes['PipelineStatus'] = ResolversParentTypes['PipelineStatus']> = ResolversObject<{
  currentStage?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  pipeline?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  stages?: Resolver<Array<ResolversTypes['Stage']>, ParentType, ContextType>;
  status?: Resolver<ResolversTypes['TaskStatus'], ParentType, ContextType>;
  taskId?: Resolver<ResolversTypes['ID'], ParentType, ContextType>;
  totalStages?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type QueryResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Query'] = ResolversParentTypes['Query']> = ResolversObject<{
  agentInstances?: Resolver<Array<ResolversTypes['AgentInstance']>, ParentType, ContextType>;
  claudeAuthStatus?: Resolver<ResolversTypes['ClaudeAuthStatus'], ParentType, ContextType>;
  dashboardStats?: Resolver<ResolversTypes['DashboardStats'], ParentType, ContextType>;
  githubAuthStatus?: Resolver<ResolversTypes['GithubAuthStatus'], ParentType, ContextType>;
  githubRepositories?: Resolver<Array<ResolversTypes['GithubRepo']>, ParentType, ContextType>;
  pipelineStatus?: Resolver<Maybe<ResolversTypes['PipelineStatus']>, ParentType, ContextType, RequireFields<QueryPipelineStatusArgs, 'taskId'>>;
  repositories?: Resolver<Array<ResolversTypes['Repository']>, ParentType, ContextType>;
  repository?: Resolver<Maybe<ResolversTypes['Repository']>, ParentType, ContextType, RequireFields<QueryRepositoryArgs, 'name'>>;
  task?: Resolver<Maybe<ResolversTypes['Task']>, ParentType, ContextType, RequireFields<QueryTaskArgs, 'id'>>;
  tasks?: Resolver<ResolversTypes['TaskConnection'], ParentType, ContextType, Partial<QueryTasksArgs>>;
}>;

export type RepositoryResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Repository'] = ResolversParentTypes['Repository']> = ResolversObject<{
  branch?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  cloneDir?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  cloneStatus?: Resolver<ResolversTypes['CloneStatus'], ParentType, ContextType>;
  deployPublicKey?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  errorMessage?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  headSha?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  lastClonedAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  lastPulledAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  name?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  pollers?: Resolver<Array<ResolversTypes['String']>, ParentType, ContextType>;
  taskCount?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  url?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type RepositoryCountResolvers<ContextType = Context, ParentType extends ResolversParentTypes['RepositoryCount'] = ResolversParentTypes['RepositoryCount']> = ResolversObject<{
  count?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  repository?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type RepositoryPayloadResolvers<ContextType = Context, ParentType extends ResolversParentTypes['RepositoryPayload'] = ResolversParentTypes['RepositoryPayload']> = ResolversObject<{
  errors?: Resolver<Maybe<Array<ResolversTypes['Error']>>, ParentType, ContextType>;
  repository?: Resolver<Maybe<ResolversTypes['Repository']>, ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type StageResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Stage'] = ResolversParentTypes['Stage']> = ResolversObject<{
  agent?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  agentVersion?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  category?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  completedAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  errorMessage?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  id?: Resolver<ResolversTypes['ID'], ParentType, ContextType>;
  rawOutput?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  retryCount?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  stageNumber?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  startedAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  status?: Resolver<ResolversTypes['StageStatus'], ParentType, ContextType>;
  structuredOutput?: Resolver<Maybe<ResolversTypes['JSON']>, ParentType, ContextType>;
  taskId?: Resolver<ResolversTypes['ID'], ParentType, ContextType>;
  tokensInput?: Resolver<Maybe<ResolversTypes['Int']>, ParentType, ContextType>;
  tokensOutput?: Resolver<Maybe<ResolversTypes['Int']>, ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type StageStatusResolvers = { COMPLETED: 'completed', EXECUTING: 'executing', FAILED: 'failed', PENDING: 'pending', SKIPPED: 'skipped' };

export type SubscriptionResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Subscription'] = ResolversParentTypes['Subscription']> = ResolversObject<{
  pipelineProgress?: SubscriptionResolver<ResolversTypes['PipelineStatus'], "pipelineProgress", ParentType, ContextType, RequireFields<SubscriptionPipelineProgressArgs, 'taskId'>>;
  taskUpdated?: SubscriptionResolver<ResolversTypes['Task'], "taskUpdated", ParentType, ContextType, Partial<SubscriptionTaskUpdatedArgs>>;
}>;

export type TaskResolvers<ContextType = Context, ParentType extends ResolversParentTypes['Task'] = ResolversParentTypes['Task']> = ResolversObject<{
  assignedAgent?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  completedAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  context?: Resolver<Array<ResolversTypes['ContextEntry']>, ParentType, ContextType>;
  createdAt?: Resolver<ResolversTypes['DateTime'], ParentType, ContextType>;
  currentStage?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  errorMessage?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  id?: Resolver<ResolversTypes['ID'], ParentType, ContextType>;
  initialContext?: Resolver<Maybe<ResolversTypes['JSON']>, ParentType, ContextType>;
  pipeline?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  priority?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  repository?: Resolver<ResolversTypes['Repository'], ParentType, ContextType>;
  retryCount?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  source?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  sourceRef?: Resolver<Maybe<ResolversTypes['String']>, ParentType, ContextType>;
  stages?: Resolver<Array<ResolversTypes['Stage']>, ParentType, ContextType>;
  startedAt?: Resolver<Maybe<ResolversTypes['DateTime']>, ParentType, ContextType>;
  status?: Resolver<ResolversTypes['TaskStatus'], ParentType, ContextType>;
  title?: Resolver<ResolversTypes['String'], ParentType, ContextType>;
  updatedAt?: Resolver<ResolversTypes['DateTime'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type TaskConnectionResolvers<ContextType = Context, ParentType extends ResolversParentTypes['TaskConnection'] = ResolversParentTypes['TaskConnection']> = ResolversObject<{
  nodes?: Resolver<Array<ResolversTypes['Task']>, ParentType, ContextType>;
  totalCount?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type TaskPayloadResolvers<ContextType = Context, ParentType extends ResolversParentTypes['TaskPayload'] = ResolversParentTypes['TaskPayload']> = ResolversObject<{
  errors?: Resolver<Maybe<Array<ResolversTypes['Error']>>, ParentType, ContextType>;
  task?: Resolver<Maybe<ResolversTypes['Task']>, ParentType, ContextType>;
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;

export type TaskStatusResolvers = { BLOCKED: 'blocked', COMPLETED: 'completed', EXECUTING: 'executing', FAILED: 'failed', PENDING: 'pending', QUEUED: 'queued', TIMEOUT: 'timeout' };

export type Resolvers<ContextType = Context> = ResolversObject<{
  AgentInstance?: AgentInstanceResolvers<ContextType>;
  ClaudeAuthStatus?: ClaudeAuthStatusResolvers<ContextType>;
  ClaudeLoginResult?: ClaudeLoginResultResolvers<ContextType>;
  ClaudeLoginStart?: ClaudeLoginStartResolvers<ContextType>;
  CloneStatus?: CloneStatusResolvers;
  ContextEntry?: ContextEntryResolvers<ContextType>;
  DashboardStats?: DashboardStatsResolvers<ContextType>;
  DateTime?: GraphQLScalarType;
  Error?: ErrorResolvers<ContextType>;
  GithubAuthStatus?: GithubAuthStatusResolvers<ContextType>;
  GithubDeviceCode?: GithubDeviceCodeResolvers<ContextType>;
  GithubLoginResult?: GithubLoginResultResolvers<ContextType>;
  GithubRepo?: GithubRepoResolvers<ContextType>;
  JSON?: GraphQLScalarType;
  Mutation?: MutationResolvers<ContextType>;
  PipelineCount?: PipelineCountResolvers<ContextType>;
  PipelineStatus?: PipelineStatusResolvers<ContextType>;
  Query?: QueryResolvers<ContextType>;
  Repository?: RepositoryResolvers<ContextType>;
  RepositoryCount?: RepositoryCountResolvers<ContextType>;
  RepositoryPayload?: RepositoryPayloadResolvers<ContextType>;
  Stage?: StageResolvers<ContextType>;
  StageStatus?: StageStatusResolvers;
  Subscription?: SubscriptionResolvers<ContextType>;
  Task?: TaskResolvers<ContextType>;
  TaskConnection?: TaskConnectionResolvers<ContextType>;
  TaskPayload?: TaskPayloadResolvers<ContextType>;
  TaskStatus?: TaskStatusResolvers;
}>;

