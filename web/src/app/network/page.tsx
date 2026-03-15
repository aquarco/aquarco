import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Paper from '@mui/material/Paper'
import NetworkCheckIcon from '@mui/icons-material/NetworkCheck'

export default function NetworkPage() {
  return (
    <Box>
      <Typography variant="h5" fontWeight={700} gutterBottom>
        Network
      </Typography>
      <Paper
        variant="outlined"
        sx={{
          p: 6,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 2,
          color: 'text.secondary',
        }}
      >
        <NetworkCheckIcon sx={{ fontSize: 64, opacity: 0.3 }} />
        <Typography variant="h6" fontWeight={600}>
          Network Tracking — Coming Soon
        </Typography>
        <Typography variant="body2" textAlign="center" maxWidth={480}>
          This page will provide visibility into all outbound network requests made
          by AI agents. You will be able to audit external API calls, monitor token
          usage per endpoint, flag unexpected destinations, and enforce network
          policy rules for sandboxed agent environments.
        </Typography>
      </Paper>
    </Box>
  )
}
